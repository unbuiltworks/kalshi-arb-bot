"""Sequential L5 shadow DECISION layer for the live PMUS sports slate.

Issue #167 comment 5535231759: the discovery/parser bottleneck is closed
(ask-side evidence flows, fresh Q1_GATE_CLEARED/SHADOW_CANDIDATE rows
exist), so the next bottleneck is the actual L5 decision/action layer.
This module feeds the collector's own observation rows into the frozen
sequential L5 persistence-v2 policy and emits, per candidate market, an
AUDITABLE next action: relation, side, target maker price, q=1 size,
inventory effect, max pair cost, and the exact reason — then compares
each replayed hypothetical action against subsequently observed public
state (volume-delta inferred trades), never against hindsight.

Hard rules (unchanged, restated in every emission):
- the economic admission bound stays >=1c after known fees: on PMUS the
  maker fee is zero and rebates are NEVER counted, so the hard pair
  bound is 1 - MIN_EDGE = 0.99; the 0.98 spring OBSERVATION cap
  (OWNER DECISION 5533476555) additionally caps emitted targets, and
  the tighter of the two always wins — a wider cap can never admit a
  <1c-net pair;
- freshness: a 1h historical qualifier is NOT currently executable; a
  candidate is preflight-ready only when the FRESH (15m) window itself
  qualifies AND the last observation is younger than FRESH_BOOK_TTL_S;
- hypothetical maker fills are confirmed only by LATER inferred trades
  at/through the resting price ("possible" — an upper bound, queue
  position unproven); an existing quote is never a fill;
- LIVE MONEY DISARMED: this module imports no executor, holds no
  credentials, and cannot submit anything. The preflight report names
  the exact existing execution path an owner approval would use
  (src.pmus_spring_l5_probe_entry over the spring session), it never
  invokes it.
"""
from __future__ import annotations

import json
import time
from decimal import Decimal as D, ROUND_DOWN
from typing import Any, Mapping, Optional

from src import pmus_status_report as st
from src import pmus_l5_persistence_v2 as pv2
from src.polymarket_l5.action_model import state_key

ONE = D("1")
TICK = D("0.01")
# observation completion cap (OWNER DECISION 5533476555); the >=1c
# hard bound below is separate and always participates in the min()
PAIR_OBSERVATION_CAP = D("0.98")
SPORTS_TTE_DEFAULT_S = 1e9          # in-game sports: no settlement clock
FRESH_BOOK_TTL_S = 120.0            # newest row must be this fresh
DECISION_MIN_GAP_S = 30.0           # bounded action cadence
MAX_ACTIONS = 12
MAX_DECISION_ROWS = 8               # bounded status emission
LABELS = ("SHADOW/THEORETICAL sequential L5 decision layer; DISARMED; "
          "no credentials or order path; fills are 'possible' upper "
          "bounds from later inferred trades; rebates never counted")


def hard_pair_bound() -> D:
    """>=1c after known fees: PMUS maker fee is zero, rebates excluded."""
    return ONE - st.MIN_EDGE


def _floor_tick(v: D) -> D:
    return (v / TICK).to_integral_value(rounding=ROUND_DOWN) * TICK


class Inventory:
    """Behavioral q=1 units, Decimal cost accounting. YES == market side."""

    def __init__(self) -> None:
        self.yes = 0
        self.no = 0
        self.yes_cost = D("0")
        self.no_cost = D("0")

    @property
    def residual(self) -> int:
        return self.yes - self.no

    @property
    def matched(self) -> int:
        return min(self.yes, self.no)

    def record(self, side_yes: bool, px: D) -> None:
        if side_yes:
            self.yes += 1
            self.yes_cost += px
        else:
            self.no += 1
            self.no_cost += px

    def avg(self, side_yes: bool) -> Optional[D]:
        n = self.yes if side_yes else self.no
        c = self.yes_cost if side_yes else self.no_cost
        return (c / n) if n else None

    def snapshot(self) -> dict:
        return {"yes": self.yes, "no": self.no, "matched": self.matched,
                "residual": self.residual}


def complement_target(avg_surplus: D,
                      deficit_bid: Optional[D]) -> tuple[Optional[D], dict]:
    """Maker-only completion price on the deficit side: the tightest of
    the >=1c hard bound, the 0.98 observation cap, and the join at the
    served deficit-side bid. The hard bound ALWAYS participates."""
    hard = hard_pair_bound() - avg_surplus
    cap = PAIR_OBSERVATION_CAP - avg_surplus
    info = {"hard_bound_px": str(_floor_tick(hard)),
            "observation_cap_px": str(_floor_tick(cap)),
            "max_pair_cost": str(avg_surplus + min(hard, cap))}
    px = min(hard, cap)
    if deficit_bid is not None:
        px = min(px, deficit_bid)
    px = _floor_tick(px)
    if px <= 0:
        return None, {**info, "reason": "no_legal_maker_complement"}
    return px, info


def _proposal(model: Mapping[str, Any], bid: float, inv: Inventory,
              last_rel: str) -> dict:
    stt = state_key(bid, SPORTS_TTE_DEFAULT_S, float(inv.residual))
    d = pv2.v2_dist(model, stt, last_rel or "open", "q<5")
    if d["backed_off"]:
        return {"state": stt, "off_distribution": True}
    avg_surplus = inv.avg(inv.residual > 0) if inv.residual else None
    prop = pv2.proposal(model, stt, last_rel or "open", "q<5", inv.residual,
                        str(avg_surplus) if avg_surplus else None)
    return {"state": stt, "off_distribution": False, **prop}


def _fill_evidence(trades: list, quote_t: float, side_yes: bool,
                   px: D, until: float) -> Optional[dict]:
    """First LATER inferred trade at/through the resting maker quote in
    the hitting direction — strictly causal, one trade per quote."""
    thresh = px if side_yes else ONE - px
    for t in trades:
        tt = t.get("t")
        if tt is None or tt <= quote_t or tt > until:
            continue
        if side_yes and t["dir_inferred"] == "sell" and t["px"] <= thresh:
            return {"t": tt, "px": str(t["px"])}
        if not side_yes and t["dir_inferred"] == "buy" and t["px"] >= thresh:
            return {"t": tt, "px": str(t["px"])}
    return None


def _book_at(obs: list, t: float) -> Optional[dict]:
    prior = [o for o in obs if (st._ts(o) or 0) < t]
    if not prior:
        return None
    o = prior[-1]
    ot = st._ts(o) or 0.0
    if t - ot > FRESH_BOOK_TTL_S:
        return None
    bid = D(str(o["bid"])) if o.get("bid") else None
    ask = (D(str(o["ask"])) if o.get("ask")
           and not o.get("ask_book_missing") else None)
    return {"bid": bid, "ask": ask, "t": ot}


def decide_market(slug: str, obs: list, now: float,
                  model: Mapping[str, Any],
                  window_s: float = 3600.0) -> dict:
    """Sequential no-hindsight L5 replay over one market's collector rows,
    ending with the CURRENT auditable next action."""
    cut = now - window_s
    obs = sorted((o for o in obs if (st._ts(o) or 0) >= cut),
                 key=lambda o: st._ts(o) or 0)
    trades = st.infer_trades(obs)
    inv = Inventory()
    last_rel = ""
    actions: list[dict] = []
    pair_costs: list[str] = []
    t_prev = 0.0
    for o in obs:
        t = st._ts(o)
        if t is None or t - t_prev < DECISION_MIN_GAP_S \
                or len(actions) >= MAX_ACTIONS:
            continue
        book = _book_at(obs, t)
        if book is None or book["bid"] is None:
            continue
        t_prev = t
        prop = _proposal(model, float(book["bid"]), inv, last_rel)
        if prop.get("off_distribution"):
            actions.append({"t": round(t, 1), "state": prop["state"],
                            "decision": "no_action",
                            "reason": "v2_off_distribution"})
            continue
        rel = prop["recommended_relation"]
        if rel == "sell":
            continue
        # canonical relation semantics (action_model._relation): any buy
        # from |residual| < 1 IS an open, whatever the model's label
        if inv.residual == 0:
            rel = "open"
        side_yes = (rel in ("open", "extend") and inv.residual >= 0) or \
                   (rel == "complete" and inv.residual < 0)
        if side_yes:
            px, econ = book["bid"], {}
        else:
            deficit_bid = (ONE - book["ask"]) if book["ask"] else None
            avg_s = inv.avg(True) if inv.residual > 0 else inv.avg(False)
            px, econ = complement_target(avg_s or book["bid"], deficit_bid)
            if px is None:
                actions.append({"t": round(t, 1), "state": prop["state"],
                                "decision": "no_action", **econ})
                continue
        ev = _fill_evidence(trades, t, side_yes, px,
                            min(t + st.SLOT_S, now))
        act = {"t": round(t, 1), "state": prop["state"], "relation": rel,
               "side": "YES" if side_yes else "NO",
               "target_price": str(px), "q1_size": 1,
               "fill_class": "possible" if ev else "no_fill",
               **econ}
        if ev:
            matched_before = inv.matched
            inv.record(side_yes, px)
            act["fill_latency_s"] = round(ev["t"] - t, 1)
            if inv.matched > matched_before:
                other = inv.avg(not side_yes)
                if other is not None:
                    pair_costs.append(str(_floor_tick(other + px)))
            last_rel = rel
        act["inventory_after"] = inv.snapshot()
        actions.append(act)

    # ---- the CURRENT auditable next action, from the freshest book ----
    fresh = st.analyze_market(obs, now, 900.0)
    hist = st.analyze_market(obs, now, window_s)
    last_age = (now - (st._ts(obs[-1]) or 0)) if obs else None
    book = _book_at(obs, now)
    next_action: dict[str, Any] = {"decision": "no_action"}
    if book is not None and book["bid"] is not None:
        prop = _proposal(model, float(book["bid"]), inv, last_rel)
        if prop.get("off_distribution"):
            next_action = {"decision": "no_action",
                           "reason": "v2_off_distribution",
                           "state": prop["state"]}
        else:
            rel = prop["recommended_relation"]
            # canonical semantics: any buy from |residual| < 1 is an open
            if inv.residual == 0 and rel != "sell":
                rel = "open"
            side_yes = (rel in ("open", "extend") and inv.residual >= 0) \
                or (rel == "complete" and inv.residual < 0)
            if rel == "sell":
                next_action = {"decision": "no_action",
                               "reason": "l5_sell_is_noop"}
            else:
                if side_yes:
                    px, econ = book["bid"], {
                        "max_pair_cost": str(min(hard_pair_bound(),
                                                 PAIR_OBSERVATION_CAP))}
                else:
                    deficit_bid = (ONE - book["ask"]) if book["ask"] else None
                    avg_s = inv.avg(inv.residual > 0)
                    px, econ = complement_target(avg_s or book["bid"],
                                                 deficit_bid)
                if px is None:
                    next_action = {"decision": "no_action", **econ}
                else:
                    next_action = {
                        "decision": "quote", "relation": rel,
                        "side": "YES" if side_yes else "NO",
                        "target_price": str(px), "q1_size": 1,
                        "inventory_effect": ("+1 YES" if side_yes
                                             else "+1 NO"),
                        "state": prop["state"],
                        "reason": (f"v2 recommends {rel} "
                                   f"(p={prop.get('switch_complete_p') if rel == 'complete' else prop.get('persist_extend_p')}) "
                                   "under the >=1c-after-known-fees bound"),
                        **econ,
                    }
    elif obs:
        next_action = {"decision": "no_action",
                       "reason": f"book_stale:{round(last_age or -1, 1)}s"}

    fresh_ok = fresh.get("status") in ("Q1_GATE_CLEARED",)
    executable_now = bool(fresh_ok and last_age is not None
                          and last_age <= FRESH_BOOK_TTL_S
                          and next_action.get("decision") == "quote")
    stale_reason = None
    if hist.get("status") == "Q1_GATE_CLEARED" and not fresh_ok:
        stale_reason = ("stale_qualifier_not_executable:"
                        "1h_status_without_fresh_15m_confirmation")
    return {
        "slug": slug,
        "model_sha": model.get("_sha256_16"),
        "fresh_15m_status": fresh.get("status"),
        "window_status": hist.get("status"),
        "last_observation_age_s": (round(last_age, 1)
                                   if last_age is not None else None),
        "executable_now": executable_now,
        "stale_reason": stale_reason,
        "actions_replayed": actions,
        "possible_fills": sum(1 for a in actions
                              if a.get("fill_class") == "possible"),
        "pair_costs_theoretical": pair_costs,
        "inventory_final": inv.snapshot(),
        "next_action": next_action,
        "order_entry": False, "realized_pnl": None, "labels": LABELS,
    }


def slate_decisions(by_slug: Mapping[str, list], now: Optional[float] = None,
                    model: Optional[Mapping[str, Any]] = None) -> dict:
    """Bounded decision emission for the status/heartbeat path."""
    now = time.time() if now is None else now
    model = pv2.load_v2() if model is None else model
    ranked: list[tuple[tuple, str]] = []
    for slug, obs in by_slug.items():
        a = st.analyze_market(obs, now, 900.0)
        h = st.analyze_market(obs, now, 3600.0)
        order = {"Q1_GATE_CLEARED": 0, "SHADOW_CANDIDATE": 1}
        f = order.get(a.get("status"), 2)
        g = order.get(h.get("status"), 2)
        if f == 2 and g == 2:
            continue
        ranked.append(((f, g, -(a.get("inferred_trades") or 0), slug), slug))
    ranked.sort()
    rows = [decide_market(s, by_slug[s], now, model)
            for _, s in ranked[:MAX_DECISION_ROWS]]
    preflight = next((r for r in rows if r["executable_now"]
                      and r["fresh_15m_status"] == "Q1_GATE_CLEARED"), None)
    return {
        "kind": "PMUS_SPORTS_L5_DECISION",
        "model_sha": model.get("_sha256_16"),
        "candidates_evaluated": len(rows),
        "rows": rows,
        "preflight_candidate": ({
            "slug": preflight["slug"],
            "next_action": preflight["next_action"],
            "fresh_15m_status": preflight["fresh_15m_status"],
            "execution_path_if_approved": (
                "src.pmus_spring_l5_probe_entry (spring session): "
                "operator-held PMUS_SPRING_* dual key + fresh strict "
                "READY_FOR_REVIEW evidence + category bar + durable "
                "/state/pmus-spring mount; NOT invoked from here"),
        } if preflight else None),
        "order_entry": False, "realized_pnl": None, "labels": LABELS,
    }


def emit_decisions(by_slug: Mapping[str, list],
                   now: Optional[float] = None) -> dict:
    out = slate_decisions(by_slug, now=now)
    slim = {**out, "rows": [{k: v for k, v in r.items()
                             if k != "actions_replayed"}
                            for r in out["rows"]]}
    print("PMUS_SPORTS_L5_DECISION " + json.dumps(
        slim, separators=(",", ":"), default=str), flush=True)
    return out
