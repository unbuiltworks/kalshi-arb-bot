"""Live-path shadow for the L5 -> PMUS sequential handoff.

Answers the one question left before a submission boundary: over a real
durable collector tape, WHICH single live event would the frozen L5
action model actually have traded, and what exact action sequence would
it have produced?

    python3 -m src.pmus_l5_handoff_shadow --root /state/pmus-sports --hours 1

READ ONLY and DISARMED. No venue call, no order path, no arming, no
mutation of the tape. Every economic number is THEORETICAL: `realized_pnl`
is null in every row, because nothing was submitted, filled or settled.

No-hindsight rule, enforced structurally: a DECISION at time t sees only
observations at or before t. Only the FILL SIMULATION reads t+ -- the same
convention `pmus_spring_shadow_replay.replay_slot` already uses, so the
two can be compared row for row.

Where it runs: from a repo checkout or the spring image, NOT the
read-only sports-collector image. Sizing is delegated to
`pmus_micro_executor.choose_micro_quantity` so the shadow sizes exactly
as the live executor would -- and that module carries the order
transport, which the collector image deliberately does not ship. The
shadow reads a tape; it never needs to run beside the collector.

Fill assumption (stated, not hidden): a maker order resting at price p is
assumed filled by the first later inferred print that crosses it --
`sell <= p` for a YES join, `buy >= 1-p` for a NO join -- and cancelled
unfilled after `MAKER_REST_TIMEOUT_S`. Inferred direction comes from
`pmus_status_report.infer_trades`; on the keyless collector that is
INFERRED provenance, never OBSERVED.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Mapping, Optional

from src import pmus_coverage_report as cov
from src import pmus_l5_live_handoff as ho
from src import pmus_l5_persistence_v2 as pv2
from src import pmus_micro_executor as mx
from src import pmus_sports_l5_decision as l5d
from src import pmus_status_report as st

ONE = D("1")

# A resting maker order is not held past our own freshness bound.
MAKER_REST_TIMEOUT_S = ho.FRESH_STATE_MAX_AGE_S

# Venue minimum trade quantity is NOT in the collector tape. The live
# executor reads it per market via `mx.market_micro_spec` at submit time.
# For a read-only shadow it must therefore be DECLARED, and every report
# says which source it came from -- an assumption that silently became a
# fact is how a notional cap gets widened by accident.
DECLARED_MIN_TRADE_QTY = D("0.01")


def _obs_by_slug(rows: list) -> dict:
    per: dict[str, list] = defaultdict(list)
    for r in rows:
        if r.get("kind") == "bbo_book" and r.get("slug"):
            per[r["slug"]].append(r)
    for v in per.values():
        v.sort(key=lambda o: st._ts(o) or 0.0)
    return dict(per)


def _event_of(rows: list) -> dict:
    out: dict[str, str] = {}
    for d in rows:
        if d.get("kind") != "discovery":
            continue
        for t in d.get("targets") or []:
            if t.get("slug") and t.get("event_slug"):
                out[str(t["slug"])] = str(t["event_slug"])
    return out


def rank_events(rows: list, now: float,
                model: Optional[Mapping[str, Any]] = None) -> dict:
    """Score every live event on the tape by its best market's L5
    behavioural-state similarity plus executable conditions.

    Market TYPE is not an input. A state the frozen model never visited
    scores zero however attractive the book looks, because an action we
    cannot attribute to L5 is not an L5 action.
    """
    model = pv2.load_v2() if model is None else model
    per = _obs_by_slug(rows)
    ev = _event_of(rows)
    by_event: dict[str, list] = defaultdict(list)
    for slug, obs in per.items():
        sim = ho.event_similarity(obs, now, model)
        sim["slug"] = slug
        sim["observations"] = len(obs)
        by_event[ev.get(slug, f"UNIDENTIFIED_EVENT:{slug}")].append(sim)

    events = []
    for event_slug, markets in by_event.items():
        markets.sort(key=lambda m: (m["executable"], m["similarity"]),
                     reverse=True)
        best = markets[0]
        events.append({
            "event_slug": event_slug,
            "markets_observed": len(markets),
            "executable_markets": sum(1 for m in markets if m["executable"]),
            "best_market": best["slug"],
            "best_similarity": best["similarity"],
            "best_executable": best["executable"],
            "best_reasons": best["reasons"],
            "markets": markets[:8],
        })
    events.sort(key=lambda e: (e["best_executable"], e["best_similarity"],
                               e["executable_markets"]), reverse=True)
    return {"kind": "PMUS_L5_EVENT_RANKING", "events": events,
            "events_ranked": len(events),
            "model_sha": model.get("_sha256_16")}


def select_event(ranking: Mapping[str, Any]) -> dict:
    """Exactly one event, or a refusal naming every blocker. Fail closed:
    an event with no executable market is never selected, because the
    probe is one event only and a wrong pick spends the whole budget."""
    events = ranking.get("events") or []
    if not events:
        return {"selected": None, "blockers": ["no_live_events_on_tape"]}
    top = events[0]
    if not top["best_executable"]:
        blockers = sorted({r.split(":")[0] for e in events
                           for r in e["best_reasons"]})
        return {"selected": None,
                "blockers": blockers,
                "closest_event": top["event_slug"],
                "closest_market": top["best_market"],
                "closest_reasons": top["best_reasons"]}
    return {"selected": top["event_slug"], "market": top["best_market"],
            "similarity": top["best_similarity"], "blockers": []}


def _simulate_fill(trades: list, after_t: float, px: D, side_yes: bool,
                   timeout_s: float = MAKER_REST_TIMEOUT_S) -> dict:
    """Would a maker order resting at `px` have been filled? Reads only
    prints strictly after the decision -- never before, never at."""
    deadline = after_t + timeout_s
    for t in trades:
        tt = t.get("t")
        if tt is None or tt <= after_t:
            continue
        if tt > deadline:
            break
        p = t["px"]
        if side_yes and t["dir_inferred"] == "sell" and p <= px:
            return {"filled": True, "t": round(tt, 1), "px": str(px),
                    "crossed_by": {"px": str(p), "dir": "sell",
                                   "vol": t["vol"]},
                    "provenance": "INFERRED"}
        if (not side_yes) and t["dir_inferred"] == "buy" and p >= ONE - px:
            return {"filled": True, "t": round(tt, 1), "px": str(px),
                    "crossed_by": {"px": str(p), "dir": "buy",
                                   "vol": t["vol"]},
                    "provenance": "INFERRED"}
    return {"filled": False, "reason": f"no_cross_within_{int(timeout_s)}s",
            "t": round(deadline, 1), "px": str(px), "provenance": "INFERRED"}


def shadow_market(obs: list, now: float,
                  model: Optional[Mapping[str, Any]] = None,
                  min_trade_qty: D = DECLARED_MIN_TRADE_QTY) -> dict:
    """Replay the handoff loop over one market's recorded observations.

    observe -> v2 predicts -> risk gate -> (shadow) rest -> fill? ->
    update inventory -> predict again. One market, one event, sequential.
    """
    model = pv2.load_v2() if model is None else model
    obs = sorted(obs, key=lambda o: st._ts(o) or 0.0)
    trades = st.infer_trades(obs)
    inv = l5d.Inventory()
    rows: list = []
    spent = D("0")
    last_rel = ""
    clock = st._ts(obs[0]) if obs else None
    if clock is None:
        return {"actions": [], "inventory": inv, "spent": spent,
                "refusal": "no_timestamped_observations"}

    while len(rows) < ho.MAX_ACTIONS * 3:      # bounded; gates end it
        book = l5d._book_at(obs, clock + 0.001)
        if book is None:
            break
        if clock - book["t"] > ho.FRESH_STATE_MAX_AGE_S:
            break
        bid, ask = book.get("bid"), book.get("ask")
        if bid is None or ask is None:
            clock += ho.MIN_REEVALUATION_GAP_S
            if clock > (st._ts(obs[-1]) or 0.0):
                break
            continue

        plan = ho.next_action(bid, ask, inv, last_rel, model)
        state = {"t": round(clock, 1), "bid": str(bid), "ask": str(ask),
                 "book_age_s": round(clock - book["t"], 1),
                 "inventory_before": inv.snapshot(),
                 "state": plan.get("state"), "state_n": plan.get("state_n")}
        if plan.get("decision") != "quote":
            rows.append(ho.audit_row(clock, state, plan,
                                     plan.get("reason") or "model_no_action",
                                     None, inv))
            break

        px = D(str(plan["_px"]))
        try:
            qty = mx.choose_micro_quantity(px, min_trade_qty,
                                           ho.MAX_ACTION_NOTIONAL,
                                           ho.MAX_ACTION_NOTIONAL)
        except mx.Halt as exc:
            rows.append(ho.audit_row(clock, state, plan,
                                     f"sizing_halt:{exc}", None, inv))
            break
        notional = ho.action_notional(px, qty)
        plan = dict(plan)
        plan["qty"] = str(qty)
        plan["notional"] = str(notional)

        submitted = sum(1 for r in rows if r["refuse_reason"] is None)
        gate = ho.risk_gate(plan, inv, spent, submitted, notional)
        if gate:
            rows.append(ho.audit_row(clock, state, plan, gate, None, inv))
            break

        fill = _simulate_fill(trades, clock, px, bool(plan["_side_yes"]))
        if fill["filled"]:
            inv.record(bool(plan["_side_yes"]), px)
            spent += notional
            last_rel = str(plan.get("relation") or last_rel)
        rows.append(ho.audit_row(clock, state, plan, None, fill, inv))
        clock = max(float(fill["t"]), clock + ho.MIN_REEVALUATION_GAP_S)
        if clock > (st._ts(obs[-1]) or 0.0):
            break

    return {"actions": rows, "inventory": inv, "spent": spent,
            "trades_inferred": len(trades)}


def run(root: Path, hours: float = 1.0, now: Optional[float] = None,
        min_trade_qty: D = DECLARED_MIN_TRADE_QTY,
        min_qty_source: str = "declared_assumption") -> dict:
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    rows = cov.load_rows(root, hours, now)
    model = pv2.load_v2()
    ranking = rank_events(rows, now, model)
    choice = select_event(ranking)

    session: Optional[dict] = None
    if choice.get("selected"):
        obs = _obs_by_slug(rows).get(choice["market"], [])
        res = shadow_market(obs, now, model, min_trade_qty)
        session = ho.session_report(choice["selected"], res["actions"],
                                    res["inventory"], res["spent"])
        session["market_slug"] = choice["market"]
        session["trades_inferred"] = res.get("trades_inferred")

    return {
        "kind": "PMUS_L5_HANDOFF_SHADOW",
        "generated_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "tape_root": str(root),
        "window_hours": hours,
        "rows_loaded": len(rows),
        "armed": ho.armed(),
        "provenance": ("INFERRED (keyless collector tape; direction from "
                       "volume-delta inference, not observed fills)"),
        "fill_assumption": {
            "rule": "maker rest filled by first crossing inferred print",
            "timeout_s": MAKER_REST_TIMEOUT_S,
            "no_hindsight": "decision reads t-; fill reads t+ only",
        },
        "sizing": {
            "minimum_trade_qty": str(min_trade_qty),
            "minimum_trade_qty_source": min_qty_source,
            "read_live_at_submit_by": "pmus_micro_executor.market_micro_spec",
        },
        "ranking": ranking,
        "selection": choice,
        "session": session,
        "submission_boundary": submission_boundary(choice, session),
        "realized_pnl": None,
        "labels": ho.LABELS,
    }


def submission_boundary(choice: Mapping[str, Any],
                        session: Optional[Mapping[str, Any]]) -> dict:
    """What is still missing before an owner could arm this. Fail closed:
    the default answer is NOT_READY and every blocker is named."""
    blockers = list(choice.get("blockers") or [])
    if not choice.get("selected"):
        blockers.append("no_executable_event_selected")
    if session is not None:
        acts = [a for a in session["actions"]
                if a["refuse_reason"] is None]
        if not acts:
            blockers.append("shadow_produced_no_admissible_action")
        if not any(a.get("fill", {}).get("filled") for a in acts):
            blockers.append("shadow_produced_no_simulated_fill")
    blockers.append("provenance_inferred_not_observed")
    blockers.append("venue_minimum_trade_qty_unread_from_tape")
    if not ho.armed():
        blockers.append("handoff_disarmed_by_design")
    return {"ready": False, "blockers": sorted(set(blockers)),
            "arming": "operator-held dual key only; never armed from here"}


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--hours", type=float, default=1.0)
    ap.add_argument("--min-trade-qty", default=None,
                    help="venue minimum trade quantity; absent, a declared "
                         "assumption is used and labelled as one")
    args = ap.parse_args(argv)
    minq = (D(str(args.min_trade_qty)) if args.min_trade_qty
            else DECLARED_MIN_TRADE_QTY)
    src = "operator_supplied" if args.min_trade_qty else "declared_assumption"
    print(json.dumps(run(args.root, args.hours, None, minq, src),
                     indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
