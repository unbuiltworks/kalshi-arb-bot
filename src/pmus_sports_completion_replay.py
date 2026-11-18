"""Replay of the sports lane's stranded legs: production recovery vs the
SHARED completion policy at candidate sports caps (owner 2026-11-18).

READ-ONLY and pure: input is the lane's own tape (JSON lines it already
writes to its volume -- book frames and its ORDER/FILL/RECOVERY events) plus
public settlement prices; output is a summary in the same schema as the
BTC5m replay (decision points, stage distribution, action distribution,
refusal reasons, pair cost of each permissible completion).

LABELS, kept apart on every row:
  RECORDED  book frames, the strand's start/lead/need, production's own
            outcome and pair cost, and public settlement prices.
  MODELLED  whether a resting policy completion WOULD have filled: a maker
            bid at px is counted filled at the first LATER frame whose
            complement ask is <= px ("touch"), or < px ("through", the
            stricter variant). A modelled fill is never realized profit.
Hypothetical policy P&L = modelled pair net, or, for a leg the policy never
pairs, the held leg at its RECORDED settlement. Production P&L is RECORDED.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from decimal import Decimal as D
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from src import pmus_sports_completion as SC

ONE = D(1)
TICK = D("0.01")


def _px(level: Any) -> Optional[D]:
    if isinstance(level, Mapping):
        for k in ("px", "price", "p"):
            if k in level:
                v = level[k]
                if isinstance(v, Mapping):
                    v = v.get("value")
                try:
                    return D(str(v))
                except Exception:                      # noqa: BLE001
                    return None
        return None
    if isinstance(level, (list, tuple)) and level:
        try:
            return D(str(level[0]))
        except Exception:                              # noqa: BLE001
            return None
    return None


def load(lines: Iterable[str]) -> Tuple[Dict[str, List[Tuple[float, D, D]]],
                                        List[Dict[str, Any]]]:
    """(books {slug: [(t, yes_bid, yes_ask)]}, events) from tape lines."""
    books: Dict[str, List[Tuple[float, D, D]]] = defaultdict(list)
    events: List[Dict[str, Any]] = []
    for line in lines:
        try:
            r = json.loads(line)
        except Exception:                              # noqa: BLE001
            continue
        if not isinstance(r, dict):
            continue
        if r.get("k") == "book":
            b = _px((r.get("bids") or [None])[0])
            a = _px((r.get("asks") or [None])[0])
            if b is not None and a is not None and a > b:
                books[str(r.get("slug"))].append((float(r["t"]), b, a))
        elif r.get("k") == "ev":
            events.append(r)
    for v in books.values():
        v.sort(key=lambda x: x[0])
    events.sort(key=lambda e: float(e.get("t") or 0))
    return books, events


def strands(events: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """One record per RECOVERY_RESOLVED, with lead/need/late from that
    strand's recorded RECOVERY_DECISIONs."""
    dec = defaultdict(list)
    closed: Dict[str, float] = {}
    for e in events:
        if e.get("kind") == "RECOVERY_DECISION":
            dec[str(e.get("slug"))].append(e)
        elif e.get("kind") == "market_closed":
            closed.setdefault(str(e.get("slug")), float(e["t"]))
    out = []
    for e in events:
        if e.get("kind") != "RECOVERY_RESOLVED":
            continue
        slug = str(e.get("slug"))
        t1 = float(e["t"])
        t0 = t1 - float(e.get("age_s") or 0)
        ds = [d for d in dec[slug] if t0 - 5 <= float(d["t"]) <= t1 + 5]
        if not ds or e.get("lead_px") is None:
            continue
        first = ds[0]
        late_t = next((float(d["t"]) for d in dec[slug]
                       if d.get("late") and float(d["t"]) >= t0), None)
        out.append({
            "slug": slug, "t0": t0, "t1": t1,
            "lead": str(first.get("lead")),
            "lead_px": D(str(e.get("lead_px"))),
            "need": D(str(first.get("need") or "1")),
            "outcome": str(e.get("outcome")),
            "pair_cost": (D(str(e["pair_cost"])) if e.get("pair_cost")
                          is not None else None),
            "late_t": late_t, "closed_t": closed.get(slug)})
    return out


def held_settle(lead: str, yes_settle: Optional[D]) -> Optional[D]:
    if yes_settle is None:
        return None
    return yes_settle if lead == "YES" else ONE - yes_settle


def production_net(s: Mapping[str, Any], yes_settle: Optional[D]
                   ) -> Optional[D]:
    """RECORDED: what production did with this strand."""
    if s["outcome"] == "paired" and s["pair_cost"] is not None:
        return s["need"] * (ONE - s["pair_cost"])
    hs = held_settle(s["lead"], yes_settle)
    return None if hs is None else s["need"] * (hs - s["lead_px"])


def simulate(s: Mapping[str, Any], frames: List[Tuple[float, D, D]], *,
             sports_cap: D, fill: str = "touch") -> Dict[str, Any]:
    """Walk the strand's recorded frames under the shared policy."""
    resting: Optional[D] = None
    pts: List[Dict[str, Any]] = []
    for t, yb, ya in frames:
        if t < s["t0"]:
            continue
        if s.get("closed_t") and t > s["closed_t"]:
            break
        comp_ask = (ONE - yb) if s["lead"] == "YES" else ya
        if resting is not None and (
                comp_ask <= resting if fill == "touch" else comp_ask < resting):
            return {"filled": True, "px": resting, "t": t,
                    "pair_cost": s["lead_px"] + resting, "points": pts}
        late = s.get("late_t") is not None and t >= s["late_t"]
        d = SC.decide(lead=s["lead"], lead_px=s["lead_px"], need=s["need"],
                      yes_bid=yb, yes_ask=ya, tick=TICK, late=late,
                      sports_cap=sports_cap, resting_px=resting)
        pol = d.get("policy") or {}
        mk = (pol.get("options") or {}).get("MAKER_COMPLETE") or {}
        pts.append({"t": t, "stage": (d.get("stage") or {}).get("stage_name"),
                    "action": "MAKER_COMPLETE" if d["px"] is not None
                    else "HOLD",
                    "refusal": None if d["px"] is not None else
                    (mk.get("why") or d.get("why")),
                    "pair_cost": str(s["lead_px"] + d["px"])
                    if d["px"] is not None else None})
        resting = d["px"]
    return {"filled": False, "points": pts}


def run(books: Mapping[str, List[Tuple[float, D, D]]],
        events: List[Mapping[str, Any]],
        settlements: Mapping[str, Optional[D]], *,
        caps=(D("0.97"), D("0.99")), fills=("touch", "through")
        ) -> Dict[str, Any]:
    """Offline only: enables the shared policy INSIDE this call (the live
    lane's own switch is untouched and the runner refuses when it is on)."""
    from unittest import mock
    from src import l5_completion_policy as CP
    with mock.patch.object(CP, "enabled", lambda: True):
        return _run(books, events, settlements, caps=caps, fills=fills)


def _run(books, events, settlements, *, caps, fills) -> Dict[str, Any]:
    st = strands(list(events))
    prod = {"strands": len(st), "paired": 0, "held": 0, "net_usd": D(0),
            "unsettled": 0, "pair_costs": []}
    for s in st:
        ys = settlements.get(s["slug"])
        n = production_net(s, ys)
        if s["outcome"] == "paired":
            prod["paired"] += 1
            prod["pair_costs"].append(s["pair_cost"])
        else:
            prod["held"] += 1
        if n is None:
            prod["unsettled"] += 1
        else:
            prod["net_usd"] += n
    out: Dict[str, Any] = {
        "production_RECORDED": _fmt_prod(prod), "policy": {},
        "labels": ("production = RECORDED fills/pair costs + RECORDED "
                   "settlement. policy completions are MODELLED fills on "
                   "RECORDED frames; never realized profit.")}
    for cap in caps:
        for fm in fills:
            agg = {"decision_points": 0, "stages": Counter(),
                   "actions": Counter(), "refusals": Counter(),
                   "completion_pair_costs": [], "paired_MODELLED": 0,
                   "held_to_settlement": 0, "unsettled": 0,
                   "net_usd_MODELLED": D(0), "no_frames": 0}
            for s in st:
                fr = books.get(s["slug"]) or []
                if not fr:
                    agg["no_frames"] += 1
                    continue
                r = simulate(s, fr, sports_cap=cap, fill=fm)
                for p in r["points"]:
                    agg["decision_points"] += 1
                    agg["stages"][p["stage"]] += 1
                    agg["actions"][p["action"]] += 1
                    if p["refusal"]:
                        agg["refusals"][p["refusal"]] += 1
                if r["filled"]:
                    agg["paired_MODELLED"] += 1
                    agg["completion_pair_costs"].append(r["pair_cost"])
                    agg["net_usd_MODELLED"] += s["need"] * (ONE -
                                                            r["pair_cost"])
                else:
                    hs = held_settle(s["lead"], settlements.get(s["slug"]))
                    if hs is None:
                        agg["unsettled"] += 1
                    else:
                        agg["held_to_settlement"] += 1
                        agg["net_usd_MODELLED"] += s["need"] * (
                            hs - s["lead_px"])
            out["policy"][f"cap_{cap}_{fm}"] = _fmt(agg)
    return out


def _pc_stats(pcs: List[D]) -> Dict[str, Any]:
    if not pcs:
        return {"n": 0}
    s = sorted(pcs)
    return {"n": len(s), "mean": str(round(sum(s) / len(s), 4)),
            "min": str(s[0]), "median": str(s[len(s) // 2]),
            "max": str(s[-1]),
            "above_1": sum(1 for x in s if x > ONE)}


def _fmt_prod(p: Mapping[str, Any]) -> Dict[str, Any]:
    return {"strands": p["strands"], "paired": p["paired"],
            "held": p["held"], "unsettled": p["unsettled"],
            "net_usd": str(round(p["net_usd"], 4)),
            "pair_cost": _pc_stats(p["pair_costs"])}


def _fmt(a: Mapping[str, Any]) -> Dict[str, Any]:
    return {"decision_points": a["decision_points"],
            "stage_distribution": dict(a["stages"]),
            "action_distribution": dict(a["actions"]),
            "refusal_reasons": dict(a["refusals"]),
            "completion_pair_cost": _pc_stats(a["completion_pair_costs"]),
            "paired_MODELLED": a["paired_MODELLED"],
            "held_to_settlement": a["held_to_settlement"],
            "unsettled": a["unsettled"], "no_frames": a["no_frames"],
            "net_usd_MODELLED": str(round(a["net_usd_MODELLED"], 4))}
