"""LANE_A_FIRST_PROBE_DECISION — A (wait for <=0.96) vs B (allow <=0.98 once).

NOT a backtest, not a sweep, not an optimisation. Two named choices scored
against evidence ALREADY COLLECTED and frozen at
artifacts/l5_lane_a/EVIDENCE_SNAPSHOT_FROZEN.json. Nothing here collects,
tunes, arms or deploys.

THE ONE FRAMING THAT MATTERS, AND IT IS EASY TO GET BACKWARDS. B is NOT a
proposal to move a threshold. `pmus_spring_l5_session.SPRING_PAIR_TARGET` is
ALREADY 0.98, with a hard 1.00 ceiling, and has been for the whole deployed
life of this code. The operating model's ~0.95-0.96 is a SPRING-DISCIPLINE
PREFERENCE describing L5's historical spring economics; CLAUDE.md itself says
"roughly <= 0.96 WHERE THE VENUE IMPLEMENTATION USES THAT TARGET", and this
venue implementation does not -- it uses 0.98. So the real question is whether
to hold a first bounded probe to a bar STRICTER than the code it would run.
Neither option edits a constant.

B IS NOT A CLAIM THAT 0.98 IS OPTIMAL. It is a narrowly scoped first-probe
eligibility envelope, justified only by the strict sports completions presently
observed, and it expires with the probe.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

LABEL = "LANE_A_FIRST_PROBE_DECISION"
SNAPSHOT = "artifacts/l5_lane_a/EVIDENCE_SNAPSHOT_FROZEN.json"

OPTION_A = "WAIT_FOR_LE_096"
OPTION_B = "ALLOW_LE_098_FOR_FIRST_BOUNDED_PROBE"

# Deployed constants. Read, never written.
DEPLOYED_PAIR_TARGET = D("0.98")      # pmus_spring_l5_session.SPRING_PAIR_TARGET
DEPLOYED_PAIR_CEILING = D("1.00")     # hard
OPERATING_MODEL_PREFERENCE = D("0.96")


def snapshot(path: str = SNAPSHOT) -> Dict[str, Any]:
    return json.load(open(path))


def evidence(path: str = SNAPSHOT) -> Dict[str, Any]:
    """The frozen facts both options are scored against."""
    from analysis.l5.lanes import lane_a_readiness as R
    s = snapshot(path)
    rows = s["spring_watch"]
    costs: List[str] = []
    for r in rows:
        costs.extend(r.get("spring_pair_costs") or ())
    c = R.pair_cost_counts(costs)
    bar = R.category_bar(rows)
    return {
        "frozen_at_utc": s["frozen_at_utc"], "sha256": s.get("sha256"),
        "watch_rows": len(rows),
        "completions": c["n_completions"],
        "le_096": c["le_096_completions"],
        "le_098": c["le_098_completions"],
        "above_098": c["above_098_completions"],
        "cheapest": c["cheapest"],
        "distribution": {k: costs.count(k) for k in sorted(set(costs))},
        "category_bar_met": bar["any_bar_met"],
        "bar_category": (bar["closest"] or {}).get("category"),
        "bar_distinct_markets": (bar["closest"] or {}).get("distinct_markets"),
    }


def envelope(entry_price: Any = "0.27") -> Dict[str, Any]:
    """Identical under A and B. Neither option changes size or risk."""
    from analysis.l5.lanes import lane_a_readiness as R
    from src import pmus_l5_ramp_ladder as ladder
    rung = ladder.active()
    size = R.size_resolution(entry_price)
    entry = D(size["ladder"][2]["value"])
    cap = D(str(rung["per_market_cap_usd"]))
    return {
        "rung": rung["id"],
        "executable_contracts": size["ladder"][1]["value"],
        "entry_notional_usd": str(entry),
        "max_first_market_spend_usd": str(cap),
        "max_actions_per_session": rung["max_actions_per_session"],
        "residual_units_bound": rung["residual_units"],
        "concurrency": rung["concurrency"],
        "markets_per_campaign": rung["markets_per_campaign"],
        "campaigns_per_day": rung["campaigns_per_day"],
        # worst case: every dollar spent lands one-sided and settles worthless
        "max_loss_one_market_usd": str(cap),
        "max_loss_per_day_usd": str(cap * D(str(rung["markets_per_campaign"]))
                                    * D(str(rung["campaigns_per_day"]))),
        "identical_under_A_and_B": True,
    }


def locked_edge(pair_cost: Any, entry_price: Any = "0.27") -> Dict[str, Any]:
    """What a completed pair at this cost actually locks, at R1 size."""
    from analysis.l5.lanes import lane_a_readiness as R
    size = R.size_resolution(entry_price)
    qty = D(size["ladder"][1]["value"])
    per_unit = D("1") - D(str(pair_cost))
    return {"pair_cost": str(pair_cost), "edge_per_unit": str(per_unit),
            "contracts": str(qty),
            "locked_edge_usd": str((per_unit * qty).quantize(D("0.000001")))}


def options(path: str = SNAPSHOT) -> Dict[str, Any]:
    ev = evidence(path)
    env = envelope()
    return {
        "label": LABEL,
        "evidence_frozen": ev,
        "envelope_common_to_both": env,
        "options": {
            OPTION_A: {
                "eligibility": "complete only at pair cost <= 0.96",
                "prospective_support": (
                    f"NONE in the frozen sample: 0 of {ev['completions']} strict "
                    f"completions are <= 0.96; cheapest observed is "
                    f"{ev['cheapest']}."),
                "historical_support": (
                    "STRONG. L5's window-level effective matched pair cost has "
                    "median 0.9648, and the operating model records spring "
                    "patience targeting ~0.95-0.96. Our own earlier PMUS posted "
                    "MAKER pair p50 was 0.9627 over 48 contemporaneous "
                    "candidates."),
                "historical_contradiction": (
                    "Those same records carry a SURVIVORSHIP WARNING, "
                    "classified UNKNOWN: n=9 decomposed pairs, our fills landing "
                    "at pair p50 0.949 and our NO-FILLS at 0.9952. The cheap "
                    "pairs are the ones that filled, so 0.96 may describe "
                    "selection rather than attainable cost."),
                "what_the_probe_would_resolve": "nothing yet -- it does not run",
                "cost_of_choosing": (
                    "the arrival rate of a <=0.96 strict sports completion is "
                    "UNMEASURED. 0 of 13 gives no rate, only an upper bound, so "
                    "this is an open-ended wait during which execution, "
                    "queue and accounting questions stay unresolved."),
            },
            OPTION_B: {
                "eligibility": (
                    "complete at pair cost <= 0.98 for ONE bounded first probe; "
                    "scoped to this probe and expiring with it"),
                "not_a_threshold_change": (
                    "SPRING_PAIR_TARGET is ALREADY 0.98 with a hard 1.00 "
                    "ceiling. B edits no constant; it declines to hold the "
                    "probe to a bar stricter than the deployed code."),
                "not_optimal": (
                    "0.98 is NOT claimed optimal, and no sweep supports it. It "
                    "is a first-probe eligibility envelope justified only by the "
                    "strict sports completions presently observed."),
                "prospective_support": (
                    f"{ev['le_098']} of {ev['completions']} strict completions "
                    f"are <= 0.98, across {ev['bar_distinct_markets']} distinct "
                    f"{ev['bar_category']} markets, all fee-safe by the "
                    "recorder's own accounting."),
                "historical_support": (
                    "L5's switch pair price median is 0.99 with 47.2% ABOVE "
                    "1.00, and 36.2% of his two-sided windows end above par. A "
                    "0.98 ceiling still refuses the top third to half of what he "
                    "paid."),
                "historical_contradiction": (
                    "STRONG AND EXPLICIT. The operating model holds that near-$1 "
                    "pair tolerance is INCENTIVE-ERA behavior and must stay "
                    "locked 'unless equivalent economics are source-confirmed on "
                    "our account'. We do hold source-confirmed credited rebates "
                    "-- $9.67 across 790 of 1,186 fills -- but those were earned "
                    "at historical fill sizes orders of magnitude above R1. At "
                    "0.02 contracts the rebate does not rescue a 0.98 pair, so "
                    "the incentive support that would justify near-$1 tolerance "
                    "is NOT present at this size."),
                "what_the_probe_would_resolve": [
                    "does our maker quote actually rest and fill on a live PMUS "
                    "sports book, at a known queue position",
                    "does the complement complete, and at what latency",
                    "what the venue actually charges and credits on OUR fills "
                    "at R1 size, joined to the exact fill",
                    "does filled -> settled -> realized close end-to-end on a "
                    "market this probe itself opened",
                ],
                "what_it_would_NOT_resolve": (
                    "profitability. At 0.98 the locked edge is "
                    f"{locked_edge('0.98')['locked_edge_usd']} on the executable "
                    "size -- statistical noise. Any P&L from this probe is a "
                    "measurement artefact and must never be read as expectancy."),
            },
        },
    }


# ------------------------------------- hypothetical preflight, option B only

HYPO_LABEL = "LANE_A_B_PREFLIGHT/HYPOTHETICAL_NOT_EXECUTED"


def hypothetical_b_preflight(path: str = SNAPSHOT, *,
                             serving_artifact: str = "6adcc4c43e67",
                             ) -> Dict[str, Any]:
    """Run the deployed-artifact preflight AS IF option B were chosen.

    HYPOTHETICAL. Nothing is cleared, armed, deployed or ordered. The shutdown
    latch and the dual key are represented as SET-AGAINST-US -- the only two
    owner-controlled gates -- and every other gate is evaluated against the
    frozen evidence exactly as it stands.
    """
    from analysis.l5.lanes import lane_a_readiness as R
    s = snapshot(path)
    rows = s["spring_watch"]
    v = R.arming_preflight(
        rows=rows, entry_price="0.27", minimum_trade_qty="0.01",
        serving_artifact=serving_artifact,
        budget_permits=True,               # observed True since the UTC rollover
        latch_set=True,                    # OWNER-CONTROLLED, not cleared here
        dual_key_set=False,                # OWNER-CONTROLLED, not armed here
        settlement_confirmed_markets=34,   # Ledger D, venue-confirmed
    )
    owner = {"SHUTDOWN_LATCH_CLEARED", "DUAL_KEY_ARMED"}
    blocked = {b["gate"] for b in v["blockers"]}
    return {
        "label": HYPO_LABEL,
        "option": OPTION_B,
        "evidence_sha256": s.get("sha256"),
        "frozen_at_utc": s.get("frozen_at_utc"),
        "serving_artifact": serving_artifact,
        "gates": v["gates"],
        "blockers": v["blockers"],
        "owner_controlled_gates": sorted(owner),
        "evidence_gates_all_pass": not (blocked - owner),
        "only_owner_gates_remain": blocked == owner,
        "pair_eligibility_applied": str(DEPLOYED_PAIR_TARGET),
        "pair_eligibility_is_deployed_constant": True,
        "owner_decisions": v["owner_decisions"],
        "labels": ("HYPOTHETICAL. No latch cleared, no key armed, no order "
                   "placed, no deployment. Passing every evidence gate is not "
                   "an instruction to trade."),
    }
