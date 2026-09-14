"""Run the SAME canonical L5 behavioural shadow over observed sports markets.

There is no sports strategy here. `continuous_inventory.replay_window` is the
same replay that produced the Gemini 5m result, driving the same unchanged
`pmus_l5_persistence_v2` lookup through `l5_venue_adapter.proposal_v2`. The
only thing that changes per cohort is which venue adapter normalises the book
-- and that is exactly the adapter the live engine uses on that venue.

L5_BEHAVIORAL_ACTION and VENUE_ADMISSIBLE_ACTION stay separate, as they do in
the crypto run, so a venue refusing an action is never read as L5 declining
to take one.

CADENCE IS A HARD LIMIT AND IS REPORTED, NOT HIDDEN. PMUS is polled against a
rate budget, so its sweep period is several seconds. A shadow cannot observe
a 1-4 s state transition it never sampled, and no amount of replay recovers
it. Every cohort therefore carries its own `cadence_s`, and a cohort whose
cadence exceeds the 1-4 s band is marked CADENCE_LIMITED so its action counts
are read as a LOWER BOUND rather than a measurement.
"""
from __future__ import annotations

import sys
from typing import Any, Dict, List, Mapping, Optional

sys.path.insert(0, __file__.rsplit("/analysis/", 1)[0])
from analysis.l5.gemini_trajectory import continuous_inventory as CI  # noqa: E402

# Above this sweep period the 1-4 s transition band is unobservable.
CADENCE_LIMIT_S = 4.0


def to_window(key: str, entry: Mapping[str, Any]) -> Optional[dict]:
    """One observed market -> the shadow's window shape.

    tte is reconstructed from the observation's own `tte_s` where the venue
    gives one; where it does not (PMUS sports have no settlement clock) a
    SYNTHETIC monotone tte is used purely to order the series, and the venue
    shim is told not to use a settlement clock so the POLICY still sees the
    coarsest bucket exactly as it does live.
    """
    obs = entry.get("obs") or []
    if len(obs) < 2:
        return None
    s = entry.get("summary") or {}
    exp = s.get("expiry_t")
    have_clock = exp is not None and obs[0].get("tte_s") is not None
    # a stable reference instant so tte is monotone decreasing either way
    ref = float(exp) if have_clock else float(obs[-1]["t"]) + 1.0
    series = [[round(ref - float(o["t"]), 2), o["bid"], o["ask"],
               o.get("bid_sz"), o.get("ask_sz")] for o in obs]
    prints = [[round(ref - float(p["t"]), 2), p["price"], p["qty"],
               bool(p.get("taker_sold"))] for p in (entry.get("prints") or [])]
    tick = s.get("tick_size")
    return {
        "market": s.get("market_id") or key,
        "asset": s.get("sport"), "expiry_t": ref,
        "window_minutes": None,
        "series": series, "print_rows": prints,
        "one_sided_obs": s.get("one_sided_observations") or 0,
        "tick_size": tick if isinstance(tick, (int, float)) else 0.01,
        "venue": s.get("venue"), "sport": s.get("sport"),
        "league": s.get("league"), "cadence_s": s.get("cadence_s"),
        "has_settlement_clock": bool(have_clock),
    }


def venue_for(win: Mapping[str, Any]) -> Any:
    if str(win.get("venue")).upper() == "PMUS":
        return CI.pmus_sports_venue()
    return CI.gemini_venue()


def run(observed: Mapping[str, Any], *, model: Optional[Mapping] = None,
        tiers: tuple = CI.TIERS) -> dict:
    from src import pmus_l5_persistence_v2 as pv2
    model = model if model is not None else pv2.load_v2()
    wins = [w for w in (to_window(k, v)
                        for k, v in (observed.get("series") or {}).items())
            if w is not None]
    out: Dict[str, Any] = {
        "model": model.get("_sha256_16"),
        "policy_source": "pmus_l5_persistence_v2 (UNCHANGED)",
        "windows": len(wins), "tiers": {}, "labels": CI.LABELS,
    }
    for tier in tiers:
        rows = []
        for w in wins:
            r = CI.replay_window(w, model=model, tier=tier,
                                 adapter=venue_for(w))
            cad = w.get("cadence_s")
            rows.append({**r, "venue": w.get("venue"), "sport": w.get("sport"),
                         "league": w.get("league"), "cadence_s": cad,
                         "cadence_limited": bool(cad and cad > CADENCE_LIMIT_S),
                         "has_settlement_clock": w["has_settlement_clock"]})
        out["tiers"][tier] = {"rows": rows, **CI.summarize(rows)}
        out["tiers"][tier].pop("windows_detail", None)
    return out


def by_cohort(result: Mapping[str, Any], tier: str = "STRICT") -> dict:
    """Aggregate one tier's shadow rows into venue x sport cohorts."""
    import collections
    rows = (result.get("tiers") or {}).get(tier, {}).get("rows") or []
    g = collections.defaultdict(list)
    for r in rows:
        g["%s/%s" % (r.get("venue"), r.get("sport"))].append(r)
    out = {}
    for k, v in g.items():
        out[k] = {
            "markets": len(v),
            "matched_campaigns": sum(1 for r in v if r["status"] == "MATCHED"),
            "residual_hold": sum(1 for r in v if r["status"] == "RESIDUAL_HOLD"),
            "no_fill": sum(1 for r in v if r["status"] == "NO_FILL"),
            "fills_total": sum(r.get("fills_n", 0) for r in v),
            "complement_fills": sum(1 for r in v for f in (r.get("fills") or [])
                                    if f["relation"] == "complete"),
            "L5_BEHAVIORAL_ACTIONS_total": sum(
                r.get("L5_BEHAVIORAL_ACTIONS", 0) for r in v),
            "VENUE_ADMISSIBLE_ACTIONS_total": sum(
                r.get("VENUE_ADMISSIBLE_ACTIONS", 0) for r in v),
            "requotes_total": sum(r.get("requotes", 0) for r in v),
            "intentions_total": sum(r.get("intentions_n", 0) for r in v),
            "prints_total": sum(r.get("prints_in_window", 0) for r in v),
            "cadence_limited": all(r.get("cadence_limited") for r in v),
            "cadence_s": v[0].get("cadence_s"),
        }
    return dict(sorted(out.items(),
                       key=lambda kv: -kv[1]["intentions_total"]))
