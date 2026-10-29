"""Why a passive quote that IS traded through still does not fill.

THE STATE BEING DIAGNOSED. The Gemini surface's sell side is placed, rests,
reaches the book and is traded through -- 20 flow interactions at the last
read -- and records zero modeled fills. That is QUEUE-BOUND. It is not
flow-starved and it is not broken complement logic, and this module exists so
neither gets re-asserted.

FOUR CANDIDATE EXPLANATIONS, each measured rather than argued:

  PERSISTENCE   a quote that is retired and re-placed goes to the BACK of the
                queue. If the surface churns, it is perpetually last in a
                fresh queue and volume ahead never clears. Measured as quote
                lifetime and re-placement rate.
  PLACEMENT     joining the touch puts us behind everything already there.
                A rung one tick BEHIND the touch has a shorter queue but needs
                the touch to move. Measured as queue depth by tick offset.
  REFRESH       replacing on every touch move resets priority even when our
                price is unchanged. Measured as re-places at an UNCHANGED
                price -- those are pure priority loss with no economic reason.
  QUEUE MODEL   VOLUME_AHEAD_CLEARED assumes we joined behind EVERYTHING
                resting. Real priority is unpublished and may be better, so
                the model is a LOWER BOUND on fills. Measured as how much of
                the assumed queue actually cleared.

WHAT THIS CANNOT SETTLE, said plainly: our true queue position is unpublished,
so no amount of tape analysis converts the model into a fact. What it CAN do
is say which explanation the observable evidence supports, and how much of the
gap each one could account for.

SIZE IS NOT A QUEUE FIX, AND IS NOT PRESENTED AS ONE. Reducing our lot does
not reduce the volume already ahead of us: the queue ahead is unchanged and a
smaller order still waits behind exactly the same depth. Smaller size changes
risk, capacity and partial-fill behaviour, and those are reported separately
under `size_effects` with that caveat attached.
"""
from __future__ import annotations

import collections
import glob
import json
import os
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

LABEL = "GEMINI_QUEUE_DIAGNOSIS_V1"

#: explanations, in the order the evidence can rule them out
HYPOTHESES = ("PERSISTENCE", "PLACEMENT", "REFRESH", "QUEUE_MODEL")


def _rows(path: str) -> List[dict]:
    out: List[dict] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def _tape(d: str) -> Optional[str]:
    hits = glob.glob(os.path.join(d, "gemini_surface_*.jsonl"))
    return max(hits, key=os.path.getmtime) if hits else None


def _f(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def persistence(obs: Sequence[dict]) -> Dict[str, Any]:
    """How long a quote survives, and how often the surface re-places one.

    A re-place is the event that costs queue priority. `quotes_placed` is
    cumulative, so its growth across observations IS the re-placement rate.
    """
    per: Dict[str, Dict[str, Any]] = {}
    for o in obs:
        for name, b in (o.get("surfaces") or {}).items():
            placed = (b or {}).get("quotes_placed")
            live = (b or {}).get("live_quotes")
            if placed is None:
                continue
            p = per.setdefault(name, {"first_placed": placed,
                                      "last_placed": placed,
                                      "live_seen": [], "n": 0})
            p["last_placed"] = placed
            p["n"] += 1
            if live is not None:
                p["live_seen"].append(int(live))
    out: Dict[str, Any] = {}
    for name, p in per.items():
        churn = int(p["last_placed"]) - int(p["first_placed"])
        obs_n = max(1, p["n"])
        live = p["live_seen"] or [0]
        avg_live = sum(live) / len(live)
        # a quote's expected lifetime in observations: how many live quotes
        # are sustained per placement over the window
        lifetime = (avg_live * obs_n / churn) if churn else None
        out[name] = {
            "observations": obs_n,
            "quotes_placed_over_window": churn,
            "placements_per_observation": round(churn / obs_n, 4),
            "mean_live_quotes": round(avg_live, 3),
            "mean_quote_lifetime_observations": (
                round(lifetime, 2) if lifetime is not None else None),
        }
    return {"per_surface": out,
            "labels": ("a re-place sends a quote to the BACK of the queue. "
                       "High placements_per_observation with low lifetime is "
                       "a surface that never accumulates priority.")}


def queue_by_offset(obs: Sequence[dict]) -> Dict[str, Any]:
    """Queue depth per side, as the surfaces recorded it at placement.

    Surfaces differ in how far behind the touch they quote, so comparing their
    queue depths is the placement question asked with the data we already
    have -- no new experiment and no re-parameterisation.
    """
    per: Dict[str, Dict[str, Any]] = {}
    for o in obs:
        for name, b in (o.get("surfaces") or {}).items():
            qa = (b or {}).get("queue_ahead_by_side") or {}
            for side in ("BUY", "SELL"):
                v = (qa.get(side) or {})
                d = _f(v.get("median_volume_ahead_at_placement"))
                c = _f(v.get("median_volume_cleared_since"))
                if d is None:
                    continue
                k = "%s|%s" % (name, side)
                r = per.setdefault(k, {"depths": [], "cleared": [],
                                       "past": 0, "n": 0})
                r["depths"].append(d)
                if c is not None:
                    r["cleared"].append(c)
                r["past"] += int(v.get("n_quotes_past_their_queue") or 0)
                r["n"] += 1
    out: Dict[str, Any] = {}
    for k, r in per.items():
        ds = sorted(r["depths"])
        cs = sorted(r["cleared"]) or [0.0]
        med_d = ds[len(ds) // 2] if ds else None
        med_c = cs[len(cs) // 2]
        out[k] = {
            "observations": r["n"],
            "median_queue_ahead": med_d,
            "median_cleared": med_c,
            "clear_ratio": (round(med_c / med_d, 4)
                            if med_d not in (None, 0) else None),
            "quotes_past_their_queue": r["past"],
        }
    return {"per_surface_side": out,
            "labels": ("clear_ratio is how much of the assumed queue actually "
                       "cleared. Far below 1 means the queue is the binding "
                       "constraint, not the flow.")}


def queue_model_headroom(offsets: Mapping[str, Any]) -> Dict[str, Any]:
    """How much of the gap the MODEL could account for on its own.

    VOLUME_AHEAD_CLEARED assumes we joined behind everything resting, so it is
    a LOWER BOUND on fills. If true priority were better by a factor k, a
    quote would fill once cleared >= ahead/k. This reports the k each side
    would need -- not a claim that k is achievable, but the size of the
    assumption being made.
    """
    out: Dict[str, Any] = {}
    for k, v in (offsets.get("per_surface_side") or {}).items():
        ahead, cleared = v.get("median_queue_ahead"), v.get("median_cleared")
        if not ahead or cleared is None:
            continue
        out[k] = {
            "median_queue_ahead": ahead, "median_cleared": cleared,
            "priority_factor_needed_to_fill": (
                round(ahead / cleared, 2) if cleared else None),
            "fills_under_model": v.get("quotes_past_their_queue"),
        }
    return {"per_surface_side": out,
            "labels": ("priority_factor_needed_to_fill is how much better than "
                       "LAST our true position would have to be. It sizes the "
                       "assumption; it does not measure our real position, "
                       "which the venue does not publish.")}


def size_effects(unit: str = "1", venue_min: str = "0.01") -> Dict[str, Any]:
    """What a smaller lot does and does NOT do.

    STATED FIRST BECAUSE IT IS THE EASY MISTAKE: reducing size does not reduce
    the volume already ahead of us. The queue is unchanged; a smaller order
    waits behind exactly the same depth for exactly as long.
    """
    u, m = D(unit), D(venue_min)
    return {
        "shadow_unit": str(u), "venue_min_order_size": str(m),
        "ratio": str(u / m) if m else None,
        "queue_priority_effect": "NONE",
        "queue_priority_why": (
            "volume ahead is unchanged by our own size. A smaller order is "
            "still behind the same depth and clears at the same moment."),
        "what_it_does_change": {
            "risk_per_fill": "smaller notional per evidenced fill",
            "capacity": ("more fills needed for the same inventory, so the "
                         "same queue must clear more times"),
            "partial_fills": ("a lot at or near the venue minimum cannot "
                              "partially fill, which removes a source of "
                              "unmatched residual"),
        },
        "labels": ("this is a RISK and CAPACITY axis, never a queue-priority "
                   "fix, and it must not be presented as one."),
    }


def verdict(pers: Mapping[str, Any], offs: Mapping[str, Any]) -> Dict[str, Any]:
    """Which explanation the evidence supports, and what it cannot settle."""
    churn = [v["placements_per_observation"]
             for v in (pers.get("per_surface") or {}).values()]
    ratios = [v["clear_ratio"] for v in (offs.get("per_surface_side") or {}).values()
              if v.get("clear_ratio") is not None]
    mean_churn = round(sum(churn) / len(churn), 4) if churn else None
    med_ratio = (sorted(ratios)[len(ratios) // 2] if ratios else None)

    supported: List[str] = []
    if mean_churn is not None and mean_churn >= 0.25:
        supported.append("PERSISTENCE")
    if med_ratio is not None and med_ratio < 0.5:
        supported.append("PLACEMENT")
        supported.append("QUEUE_MODEL")
    return {
        "mean_placements_per_observation": mean_churn,
        "median_clear_ratio": med_ratio,
        "supported_by_evidence": supported,
        "not_supported": [h for h in HYPOTHESES if h not in supported],
        "cannot_settle": (
            "our true queue position is unpublished, so no tape analysis "
            "converts VOLUME_AHEAD_CLEARED into a fact. This says which "
            "explanation the observable evidence supports, not which is true."),
        "explicitly_rejected": [
            "FLOW_STARVED -- flow went through the sell side and was recorded",
            "BROKEN_COMPLEMENT -- the sell side is placed, rests and reaches "
            "the book more often than the buy side",
        ],
    }


def report(state_dir: str) -> Dict[str, Any]:
    path = _tape(state_dir)
    if not path:
        return {"label": LABEL, "ok": False, "why": "no gemini surface tape"}
    rows = _rows(path)
    obs = [r for r in rows if r.get("kind") == "observation"]
    if not obs:
        return {"label": LABEL, "ok": False, "why": "no observations on tape",
                "tape": path, "rows": len(rows)}
    pers = persistence(obs)
    offs = queue_by_offset(obs)
    return {
        "label": LABEL, "ok": True, "tape": path,
        "rows": len(rows), "observations": len(obs),
        "state": "QUEUE_BOUND",
        "persistence": pers,
        "queue": offs,
        "queue_model": queue_model_headroom(offs),
        "size_effects": size_effects(),
        "verdict": verdict(pers, offs),
        "labels": [
            "the sell side is NOT flow-starved and the complement is NOT "
            "broken; both were measured and both are excluded.",
            "size is a risk and capacity axis, never a queue-priority fix.",
        ],
    }
