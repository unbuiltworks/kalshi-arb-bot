"""L5 -> PMUS live handoff: sequential action model driving one event.

OWNER DIRECTIVE (issue #167 comment 5547365082). The probe answers
"what would L5 do NEXT at this state?" -- it does NOT wait for a
pre-formed cheap pair. Q1_GATE_CLEARED and the <=0.99 pair bound are
preserved ONLY as execution/risk constraints on an action the model has
already chosen; neither is an entry strategy.

Loop, within ONE sporting event:

    observe live state -> v2 model predicts next action (side/price/
    size/timing) -> risk gates -> submit or refuse with a reason ->
    observe fill -> update inventory -> model predicts again

Reconciliation, not a new strategy:
  * action model      src/pmus_l5_persistence_v2.py   (frozen artifact)
  * behavioural units src/pmus_sports_l5_decision.py  (Inventory, planner)
  * order transport   src/pmus_micro_executor.py      (PR #169 engine)
  * fee model         src/pmus_q1_executor.py         (maker 0, taker fn)

DISARMED BY DEFAULT behind its OWN isolated dual key. The q1, micro and
spring keys cannot arm this, and this key cannot arm them.
"""
from __future__ import annotations

import json
import os
import time
from decimal import Decimal as D
from typing import Any, Mapping, Optional

from src import pmus_l5_persistence_v2 as pv2
from src import pmus_sports_l5_decision as l5d
from src import pmus_q1_executor as q1
from src import pmus_status_report as st

# --- isolated arming, deliberately distinct from q1/micro/spring ---
HANDOFF_LIVE_ENV = "PMUS_HANDOFF_LIVE"
HANDOFF_SID_ENV = "PMUS_HANDOFF_SESSION_ID"
HANDOFF_ARM_ENV = "PMUS_HANDOFF_ARM"
HANDOFF_EVENT_ENV = "PMUS_HANDOFF_EVENT_SLUG"

# --- hard risk envelope (item 4). Constants, not tunables. ---
MAX_ACTIONS = 6                    # total submitted actions this event
MAX_EVENT_NOTIONAL = D("0.05")     # total $ committed within the event
MAX_ACTION_NOTIONAL = D("0.01")    # per single action
MAX_RESIDUAL_UNITS = 3             # signed directional units
MAX_IMBALANCE_UNITS = 3            # |yes - no| ceiling
DAILY_REALIZED_LOSS_STOP = D("-1.00")

# OWNER DECISION 5547834091. A bounded residual is HELD to settlement and
# redeemed -- it is not flattened. This preserves L5's own measured
# behaviour ($2.18 of lifetime sells against 15,243 redeems) and
# supersedes rule R5 for this isolated replication probe only. It adds no
# exposure: the residual is already bounded by MAX_RESIDUAL_UNITS and by
# the event notional cap, and holding it changes only what happens at the
# end, not how much can be committed. There is deliberately NO flatten
# path in this module; a residual settling to zero is accounted honestly
# as directional residual P&L, never netted away or called locked.
RESIDUAL_POLICY = "HOLD_TO_SETTLEMENT"
FRESH_STATE_MAX_AGE_S = 120.0
MIN_REEVALUATION_GAP_S = 10.0

# Volume-delta inference needs consecutive snapshots; below this many
# observations a market's flow is UNMEASURED, never measured as zero.
MIN_OBS_TO_MEASURE_FLOW = 6

# Execution/risk constraint ONLY -- never an entry condition.
PAIR_RISK_BOUND = l5d.hard_pair_bound()      # 0.99, >=1c after known fees

LABELS = ("SHADOW/THEORETICAL unless explicitly armed; submitted->filled"
          "->settled->realized; realized_pnl null until settlement; "
          "rebates never counted toward the >=1c bound")


def armed() -> bool:
    """Isolated dual key. Absent any part of it, nothing can submit."""
    sid = os.getenv(HANDOFF_SID_ENV, "").strip()
    return (os.getenv(HANDOFF_LIVE_ENV, "").strip().upper() == "YES"
            and bool(sid)
            and os.getenv(HANDOFF_ARM_ENV, "").strip() == sid)


def behavioural_state(bid: D, inv: "l5d.Inventory", last_rel: str,
                      model: Optional[Mapping[str, Any]] = None) -> dict:
    """The canonical v2 state for this live market + our own inventory."""
    if model is not None:
        return pv2.v2_dist(model,
                           l5d.state_key(float(bid),
                                         l5d.SPORTS_TTE_DEFAULT_S,
                                         float(inv.residual)),
                           last_rel or "open", "q<5")
    return pv2.v2_dist(pv2.load_v2(),
                       l5d.state_key(float(bid), l5d.SPORTS_TTE_DEFAULT_S,
                                     float(inv.residual)),
                       last_rel or "open", "q<5")


def event_similarity(obs: list, now: float,
                     model: Optional[Mapping[str, Any]] = None) -> dict:
    """Rank an event by L5 BEHAVIOURAL-STATE similarity plus executable
    conditions -- never by market type and never by a pre-formed pair.

    Similarity is how well the live state sits inside the frozen model's
    visited distribution: a state v2 never saw cannot be replicated, so
    it scores zero however attractive the book looks.
    """
    model = pv2.load_v2() if model is None else model
    a = st.analyze_market(obs, now, 900.0)
    bid, ask = a.get("bid"), a.get("ask")
    out = {"slug": None, "in_distribution": False, "similarity": 0.0,
           "executable": False, "reasons": []}
    if not a.get("observations"):
        # a market we never observed in-window is not a one-sided book --
        # calling starvation "one-sided" hides a collector problem behind
        # a market-quality reason.
        out["reasons"].append("no_observations_in_window")
        return out
    if bid is None or ask is None:
        out["reasons"].append("book_not_two_sided")
        return out
    b, k = D(str(bid)), D(str(ask))
    inv = l5d.Inventory()
    d = behavioural_state(b, inv, "open", model)
    out["state"] = d["state"]
    out["state_n"] = d["n"]
    out["in_distribution"] = not d["backed_off"]
    # similarity: model support at this state, saturating
    out["similarity"] = round(min(1.0, d["n"] / 200.0), 4)
    spread = k - b
    out["bid"], out["ask"], out["spread"] = str(b), str(k), str(spread)
    if not (l5d.D("0.35") <= b <= l5d.D("0.65")):
        out["reasons"].append(f"outside_observation_band:{b}")
    # Flow is inferred from volume deltas BETWEEN consecutive snapshots,
    # so a market the rotation visited once necessarily reads zero trades.
    # Calling that "thin flow" asserts the market does not trade when we
    # simply did not look -- the same error that once made a whole prop
    # tail read as dead. Below the measurement floor the honest answer is
    # that flow is unmeasured, and the market is unobserved, not bad.
    n_obs = a.get("observations") or 0
    if n_obs < MIN_OBS_TO_MEASURE_FLOW:
        out["reasons"].append(f"flow_unmeasured_only_{n_obs}_observations")
        out["measurable"] = False
    else:
        out["measurable"] = True
        if (a.get("inferred_trades") or 0) < 10:
            out["reasons"].append(f"thin_flow:{a.get('inferred_trades')}")
        if a.get("two_sided_inferred_flow") is not True:
            out["reasons"].append("one_directional_flow")
    if (a.get("collector_gaps") or 0) > 0:
        out["reasons"].append(f"stale_cadence:{a.get('collector_gaps')}")
    if not out["in_distribution"]:
        out["reasons"].append("state_off_distribution")
    out["executable"] = not out["reasons"]
    return out


def action_notional(price: D, qty: D) -> D:
    """Dollars committed by one action. Price is NOT notional: micro
    sizing uses a decimal quantity, so a 0.45 contract at qty 0.02 costs
    $0.009, not $0.45. Conflating the two made the event cap reject every
    action on its first call."""
    return (price * qty).quantize(D("0.000001"))


def risk_gate(plan: Mapping[str, Any], inv: "l5d.Inventory",
              spent: D, n_actions: int, notional: D,
              realized_pnl: D = D("0")) -> Optional[str]:
    """Fail-closed execution/risk envelope applied to an action the model
    has ALREADY chosen. Never used to select an action.

    `notional` is the real dollars this action commits, computed by the
    caller from venue sizing metadata -- deliberately not inferred here,
    so a sizing change cannot silently widen the cap.
    """
    if n_actions >= MAX_ACTIONS:
        return f"max_actions:{n_actions}>={MAX_ACTIONS}"
    if realized_pnl <= DAILY_REALIZED_LOSS_STOP:
        return f"daily_loss_stop:{realized_pnl}"
    px = plan.get("_px")
    if px is None:
        return "no_price"
    if notional > MAX_ACTION_NOTIONAL:
        return f"action_notional_cap:{notional}>{MAX_ACTION_NOTIONAL}"
    if spent + notional > MAX_EVENT_NOTIONAL:
        return (f"event_notional_cap:{spent + notional}"
                f">{MAX_EVENT_NOTIONAL}")
    side_yes = plan.get("_side_yes")
    nxt = inv.residual + (1 if side_yes else -1)
    if abs(nxt) > MAX_RESIDUAL_UNITS:
        return f"residual_cap:{abs(nxt)}>{MAX_RESIDUAL_UNITS}"
    # An action budget spent entirely on entries leaves a residual that
    # cannot be completed inside the event -- the probe would end legged
    # by construction. Reserve one action per unit we would then owe.
    # This only ever REFUSES an entry; it never raises a cap.
    if plan.get("relation") != "complete":
        if MAX_ACTIONS - (n_actions + 1) < abs(nxt):
            return (f"completion_capacity_reserved:"
                    f"{MAX_ACTIONS - (n_actions + 1)}<{abs(nxt)}")
    if abs(inv.yes + (1 if side_yes else 0)
           - (inv.no + (0 if side_yes else 1))) > MAX_IMBALANCE_UNITS:
        return f"imbalance_cap:>{MAX_IMBALANCE_UNITS}"
    # pair bound is a RISK constraint on completion, not an entry rule
    if plan.get("relation") == "complete":
        other = inv.avg(not side_yes)
        if other is not None and (other + D(str(px))) > PAIR_RISK_BOUND:
            return (f"pair_risk_bound:{other + D(str(px))}"
                    f">{PAIR_RISK_BOUND}")
    return None


def next_action(bid: D, ask: D, inv: "l5d.Inventory", last_rel: str,
                model: Optional[Mapping[str, Any]] = None) -> dict:
    """What would L5 do NEXT at this state? Model first, gates after."""
    model = pv2.load_v2() if model is None else model
    d = behavioural_state(bid, inv, last_rel, model)
    if d["backed_off"]:
        return {"decision": "no_action", "reason": "v2_off_distribution",
                "state": d["state"], "state_n": d["n"]}
    rels = pv2.relation_mass(d["dist"])
    rel = max(rels, key=rels.get)
    plan = l5d.plan_action(rel, inv, {"bid": bid, "ask": ask})
    plan["state"] = d["state"]
    plan["state_n"] = d["n"]
    plan["relation_p"] = round(rels.get(rel, 0.0), 4)
    return plan


def residual_settlement_accounting(inv: "l5d.Inventory",
                                   qty: D) -> dict:
    """What the HELD residual is worth at settlement, stated as a range.

    The unmatched units are directional: they pay $1 each if the side we
    hold wins and $0 if it loses. Both ends are reported because quoting
    only the loss would understate the position and quoting only the
    payout would flatter it. `realized_pnl` stays null regardless --
    nothing here is realized until authoritative settlement.
    """
    n = abs(inv.residual)
    if n == 0:
        return {"policy": RESIDUAL_POLICY, "residual_units": 0,
                "held_to_settlement": False, "realized_pnl": None}
    side_yes = inv.residual > 0
    avg = inv.avg(side_yes)
    cost = (avg * n * qty).quantize(D("0.000001")) if avg is not None \
        else None
    payout = (D(n) * qty).quantize(D("0.000001"))
    return {
        "policy": RESIDUAL_POLICY,
        "residual_units": n,
        "residual_side": "YES" if side_yes else "NO",
        "held_to_settlement": True,
        "cost_basis": None if cost is None else str(cost),
        # settles to zero: we lose exactly what we paid for these units
        "worst_case_settlement": None if cost is None else str(-cost),
        # settles in our favour: each unit redeems at $1
        "best_case_settlement": None if cost is None
        else str((payout - cost).quantize(D("0.000001"))),
        "accounting": ("directional residual P&L, reported separately "
                       "from pair-locked cash; never netted into it"),
        "realized_pnl": None,
    }


def audit_row(t: float, state: Mapping[str, Any], plan: Mapping[str, Any],
              gate: Optional[str], fill: Optional[Mapping[str, Any]],
              inv: "l5d.Inventory") -> dict:
    """Action-by-action audit (item 5) with strict status labels."""
    if gate:
        status = "shadow_refused"
    elif not armed():
        status = "shadow"
    elif fill and fill.get("filled"):
        status = "filled"
    elif fill:
        status = "submitted"
    else:
        status = "shadow"
    return {
        "t": round(t, 1),
        "observed_state": dict(state),
        "predicted_action": {k: v for k, v in plan.items()
                             if not k.startswith("_")},
        "submit_decision": ("refused" if gate else
                            ("submitted" if armed() else "shadow_only")),
        "refuse_reason": gate,
        "fill": fill,
        "inventory_after": inv.snapshot(),
        "status": status,
        "realized_pnl": None,          # null until settlement, always
        "labels": LABELS,
    }


def session_report(event_slug: str, rows: list, inv: "l5d.Inventory",
                   spent: D) -> dict:
    return {
        "kind": "PMUS_L5_LIVE_HANDOFF",
        "event_slug": event_slug,
        "armed": armed(),
        "actions": rows,
        "actions_submitted": sum(1 for r in rows
                                 if r["submit_decision"] == "submitted"),
        # Unarmed, nothing is ever "submitted". Admitted/refused is what
        # the shadow actually measures; reporting only actions_submitted
        # made a full seven-action session read as if nothing happened.
        "actions_admitted": sum(1 for r in rows
                                if r["refuse_reason"] is None),
        "actions_refused": sum(1 for r in rows
                               if r["refuse_reason"] is not None),
        "actions_filled_shadow": sum(
            1 for r in rows if (r.get("fill") or {}).get("filled")),
        "residual_uncompleted": inv.residual,
        "ended_legged": inv.residual != 0,
        "residual_policy": RESIDUAL_POLICY,
        "inventory_final": inv.snapshot(),
        "event_notional_spent": str(spent),
        "caps": {
            "max_actions": MAX_ACTIONS,
            "max_event_notional": str(MAX_EVENT_NOTIONAL),
            "max_action_notional": str(MAX_ACTION_NOTIONAL),
            "max_residual_units": MAX_RESIDUAL_UNITS,
            "max_imbalance_units": MAX_IMBALANCE_UNITS,
            "daily_realized_loss_stop": str(DAILY_REALIZED_LOSS_STOP),
            "pair_risk_bound": str(PAIR_RISK_BOUND),
            "auto_restart": False,
        },
        "fees": {"maker": "0.0000", "taker_fn": "0.06*p*(1-p)",
                 "rebates_counted_toward_bound": False},
        "model_sha": pv2.load_v2().get("_sha256_16"),
        "realized_pnl": None,
        "labels": LABELS,
    }
