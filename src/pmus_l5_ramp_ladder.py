"""Evidence-driven ramp ladder modeled on L5's April pattern (OWNER
5554127702 #4). PREBUILT, NOT ACTIVE: the campaign runs the rung named by
PMUS_SPRING_RUNG (default R1, the rung already authorized in 5553189142)
and nothing here promotes a rung by itself -- `evaluate()` turns
venue-confirmed evidence into a PROMOTE / HOLD / ROLLBACK verdict with
the exact reasons, and the owner flips the env.

Order of scaling, as L5 did it in April (analysis/l5/l5_april_ramp.json,
l5_ramp_analysis.json): actions per window and windows per day first,
size second, concurrency last. Every rung states its worst case in
dollars; the daily-loss stop (-$1.00) stays the absolute bound above all
of them and is never touched here.

Evidence keys (all from venue-confirmed rows or the campaign's own logs):
  settled_markets_clean   markets FILLED -> SETTLED -> REALIZED with
                          ledger == venue, no orphan, no halt
  fill_rate               filled orders / submitted orders
  shadow_mix_l1           L1 distance between the executed relation mix
                          and the shadow (L5-distribution) relation mix
  matched_pairs           venue-confirmed matched pairs formed
  pair_cost_max           worst matched pair cost (<= 0.98 required)
  adverse_selection_c     mean mark move AGAINST a fill 5 min after it,
                          cents (None = not measured -> fails closed)
  max_drawdown_usd        worst peak-to-trough of venue-confirmed realized
  realized_usd            venue-confirmed realized over the rung
  mismatches              inventory mismatches + orphans + halts
"""
from __future__ import annotations

import os
from decimal import Decimal as D
from typing import Any, Mapping, Optional

RUNG_ENV = "PMUS_SPRING_RUNG"
DAILY_LOSS_STOP = D("-1.00")

# per-market filled OPEN/EXTEND cap, sequential markets per campaign,
# campaigns per day the arm allows, max maker actions per session,
# concurrency; worst case = cap x markets x campaigns (complement legs
# only ever form pairs and cannot add to the directional worst case)
RUNGS: tuple[dict, ...] = (
    {"id": "R1", "name": "authorized: 3 sequential markets",
     "per_market_cap_usd": "0.05", "residual_units": 3, "markets_per_campaign": 3,
     "campaigns_per_day": 1, "max_actions_per_session": 6, "concurrency": 1,
     "lever": "baseline", "promote": {"settled_markets_clean": 3,
                                      "fill_rate_min": 0.15,
                                      "shadow_mix_l1_max": 0.30,
                                      "adverse_selection_c_max": 5.0}},
    {"id": "R2", "name": "windows/day: 6 markets, 2 campaigns",
     "per_market_cap_usd": "0.05", "residual_units": 3, "markets_per_campaign": 6,
     "campaigns_per_day": 2, "max_actions_per_session": 6, "concurrency": 1,
     "lever": "windows_per_day", "promote": {"settled_markets_clean": 8,
                                             "fill_rate_min": 0.20,
                                             "shadow_mix_l1_max": 0.25,
                                             "matched_pairs_min": 1,
                                             "adverse_selection_c_max": 4.0}},
    {"id": "R3", "name": "actions/window: 12 actions per session",
     "per_market_cap_usd": "0.05", "residual_units": 3, "markets_per_campaign": 6,
     "campaigns_per_day": 2, "max_actions_per_session": 12, "concurrency": 1,
     "lever": "actions_per_window", "promote": {"settled_markets_clean": 16,
                                                "fill_rate_min": 0.20,
                                                "shadow_mix_l1_max": 0.20,
                                                "matched_pairs_min": 3,
                                                "adverse_selection_c_max": 3.0}},
    {"id": "R4", "name": "size: $0.10 per market",
     "per_market_cap_usd": "0.10", "residual_units": 3, "markets_per_campaign": 6,
     "campaigns_per_day": 2, "max_actions_per_session": 12, "concurrency": 1,
     "lever": "size", "promote": {"settled_markets_clean": 30,
                                  "fill_rate_min": 0.25,
                                  "shadow_mix_l1_max": 0.20,
                                  "matched_pairs_min": 6,
                                  "adverse_selection_c_max": 3.0}},
    {"id": "R5", "name": "concurrency 2",
     "per_market_cap_usd": "0.10", "residual_units": 3, "markets_per_campaign": 6,
     "campaigns_per_day": 2, "max_actions_per_session": 12, "concurrency": 2,
     "lever": "concurrency", "promote": None},   # top of the prebuilt ladder
)

# OWNER 5560851070 (REPLICATE -> PROVE FIDELITY (behavioral) -> RUN): the
# smallest rung that lets materially more of the proven L5 policy reach
# the venue, derived from the R1 cohort's 203 clean decisions rather than
# picked: every one of the 36 residual-cap forcings fired at exactly 3
# units while L5's own draw at that point was EXTEND 61% of the time and
# 75% of L5's post-fill transitions sit above 5 units; the 16 spend-cap
# forcings fired at $0.054-$0.112 of filled notional. Replaying the
# cohort's measured draw masses and fill rates: residual cap 6 removes
# ~92% of the residual-cap forcing (14.5% -> 1.2% of post-fill decisions),
# a spend cap of $0.15 clears every observed spend forcing; releasing
# both moves the cohort's acted L1 from 0.54 to ~0.05-0.08 and the acted
# post-fill gap from 30 pp to ~4-5 pp. Only the two caps change: maker
# only, fee-safe 0.98, HOLD-to-settlement, concurrency 1, 6 actions per
# session, the deadman and the -$1.00 daily stop (>= the $0.90 per-campaign
# worst case) are unchanged. Outside the L5-order ladder above; never
# promoted to or from automatically.
OWNER_RUNGS: tuple[dict, ...] = (
    {"id": "R2C", "name": "caps: $0.15 per market, residual 6 units (OWNER 5560851070)",
     "per_market_cap_usd": "0.15", "residual_units": 6, "markets_per_campaign": 6,
     "campaigns_per_day": 1, "max_actions_per_session": 6, "concurrency": 1,
     "lever": "caps", "promote": None,
     "derived_from": "R1 cohort 2026-09-06, 203 clean decisions / 9 markets"},
)
ALL_RUNGS: tuple[dict, ...] = RUNGS + OWNER_RUNGS
DEFAULT_RESIDUAL_UNITS = 3


def rung(rung_id: Optional[str] = None) -> dict:
    rid = (rung_id or os.getenv(RUNG_ENV, "R1")).strip().upper()
    for r in ALL_RUNGS:
        if r["id"] == rid:
            return dict(r)
    return dict(RUNGS[0])                 # unknown -> the authorized rung


# The ACTIVE rung follows the campaign, not the process: the continuous
# session sets it from its durable state at boot and from the owner
# approval it consumes at the advance gate, so a held R1 market keeps R1
# caps while the next campaign runs under its own rung. Unset = the env
# rung (default R1).
_ACTIVE: dict = {"id": None}


def set_active(rung_id: Optional[str]) -> dict:
    r = rung(rung_id) if rung_id else None
    _ACTIVE["id"] = r["id"] if r else None
    return active()


def active() -> dict:
    return rung(_ACTIVE["id"]) if _ACTIVE["id"] else rung()


def shell_version(r: Optional[Mapping[str, Any]] = None) -> str:
    """The safety-shell label written on every live decision row
    (OWNER 5560559108 #5); R1 renders exactly the label the cohort carries."""
    r = dict(r) if r is not None else active()
    return (f"{r['id']}/cap{r['per_market_cap_usd']}"
            f"/residual{int(r.get('residual_units') or DEFAULT_RESIDUAL_UNITS)}"
            f"/conc{r['concurrency']}/maker-only/fee-safe0.98/rest30s")


def next_rung(rung_id: str) -> Optional[dict]:
    ids = [r["id"] for r in RUNGS]
    i = ids.index(rung_id) if rung_id in ids else 0
    return dict(RUNGS[i + 1]) if i + 1 < len(RUNGS) else None


def worst_case(r: Mapping[str, Any]) -> dict:
    cap = D(str(r["per_market_cap_usd"]))
    per_campaign = cap * r["markets_per_campaign"]
    per_day_raw = per_campaign * r["campaigns_per_day"]
    concurrent = cap * r["concurrency"]
    per_day = min(per_day_raw, -DAILY_LOSS_STOP)
    return {"per_market_usd": str(cap), "per_campaign_usd": str(per_campaign),
            "per_day_uncapped_usd": str(per_day_raw),
            "per_day_bounded_by_daily_stop_usd": str(per_day),
            "max_concurrent_open_exposure_usd": str(concurrent),
            "daily_loss_stop_usd": str(DAILY_LOSS_STOP)}


def ladder() -> list[dict]:
    return [{**r, "worst_case": worst_case(r)} for r in ALL_RUNGS]


def evaluate(rung_id: str, ev: Mapping[str, Any]) -> dict:
    """PROMOTE / HOLD / ROLLBACK for the rung the evidence was gathered
    on. Missing evidence never promotes (fail closed); rollback needs a
    real breach, not a missing number."""
    r = rung(rung_id)
    reasons: list[str] = []
    verdict = "HOLD"
    wc = D(str(worst_case(r)["per_campaign_usd"]))
    mism = int(ev.get("mismatches") or 0)
    realized = ev.get("realized_usd")
    dd = ev.get("max_drawdown_usd")
    if mism > 0:
        verdict, reasons = "ROLLBACK", [f"mismatches={mism}"]
    elif dd is not None and D(str(dd)) <= -wc / 2:
        verdict, reasons = "ROLLBACK", [f"drawdown {dd} <= -half worst case {wc}"]
    elif realized is not None and D(str(realized)) <= -wc / 2:
        verdict, reasons = "ROLLBACK", [f"realized {realized} <= -half worst case {wc}"]
    else:
        crit = r.get("promote")
        if crit is None:
            reasons.append("top of prebuilt ladder")
        else:
            ok = True
            n = int(ev.get("settled_markets_clean") or 0)
            if n < crit["settled_markets_clean"]:
                ok = False; reasons.append(
                    f"settled_clean {n} < {crit['settled_markets_clean']}")
            fr = ev.get("fill_rate")
            if fr is None or float(fr) < crit["fill_rate_min"]:
                ok = False; reasons.append(f"fill_rate {fr} < {crit['fill_rate_min']}")
            l1 = ev.get("shadow_mix_l1")
            if l1 is None or float(l1) > crit["shadow_mix_l1_max"]:
                ok = False; reasons.append(f"shadow_mix_l1 {l1} > {crit['shadow_mix_l1_max']}")
            mp = crit.get("matched_pairs_min")
            if mp is not None and int(ev.get("matched_pairs") or 0) < mp:
                ok = False; reasons.append(f"matched_pairs {ev.get('matched_pairs')} < {mp}")
            pc = ev.get("pair_cost_max")
            if pc is not None and D(str(pc)) > D("0.98"):
                ok = False; reasons.append(f"pair_cost_max {pc} > 0.98")
            adv = ev.get("adverse_selection_c")
            if adv is None or float(adv) > crit["adverse_selection_c_max"]:
                ok = False; reasons.append(
                    f"adverse_selection_c {adv} not measured or > {crit['adverse_selection_c_max']}")
            if ok:
                verdict = "PROMOTE"
                reasons.append("all promotion criteria met on venue-confirmed evidence")
    nxt = next_rung(r["id"])
    return {"rung": r["id"], "verdict": verdict, "reasons": reasons,
            "next_rung": nxt["id"] if nxt else None,
            "next_worst_case": worst_case(nxt) if nxt else None,
            "activation": "owner sets PMUS_SPRING_RUNG; nothing promotes itself",
            "labels": "evidence = venue-confirmed FILLED->SETTLED->REALIZED plus "
                      "the campaign's own action-by-action shadow comparison"}


def evidence_from_campaign(report: Mapping[str, Any]) -> dict:
    """Pull the evidence keys the ladder scores from a campaign report
    (the continuous session's own structure). Adverse selection is only
    filled when the report carries it; otherwise None (fails closed)."""
    sessions = report.get("sessions") or []
    orders = [o for s in sessions for o in (s.get("orders") or [])]
    filled = [o for o in orders if str(o.get("outcome")) == "filled"]
    decisions = [d for s in sessions for d in (s.get("decisions") or [])]
    acted = [d.get("acted") for d in decisions if d.get("acted") in
             ("open", "extend", "complete")]
    shadow = [((d.get("shadow") or {}).get("stochastic") or {}).get("sampled")
              for d in decisions]
    shadow = [x for x in shadow if x in ("open", "extend", "complete")]

    def _mix(xs):
        n = len(xs) or 1
        return {k: xs.count(k) / n for k in ("open", "extend", "complete")}
    l1 = (sum(abs(_mix(acted)[k] - _mix(shadow)[k]) for k in
              ("open", "extend", "complete")) if acted and shadow else None)
    settles = report.get("settlements") or []
    clean = [s for s in settles if s.get("status") == "REALIZED"]
    gate = report.get("advance_gate") or {}
    mism = int(gate.get("inventory_mismatches") or 0) + \
        int(gate.get("orphan_cancels") or 0) + int(gate.get("halts") or 0)
    pairs = sum(float((s.get("breakdown") or {}).get("matched_pairs") or 0)
                for s in settles)
    pcs = [D(str((s.get("breakdown") or {}).get("matched_pair_cost")))
           for s in settles if (s.get("breakdown") or {}).get("matched_pair_cost")]
    realized = report.get("realized_pnl")
    return {"settled_markets_clean": len(clean) if mism == 0 else 0,
            "fill_rate": (len(filled) / len(orders)) if orders else None,
            "shadow_mix_l1": round(l1, 4) if l1 is not None else None,
            "matched_pairs": pairs,
            "pair_cost_max": str(max(pcs)) if pcs else None,
            "adverse_selection_c": report.get("adverse_selection_c"),
            "max_drawdown_usd": report.get("max_drawdown_usd"),
            "realized_usd": realized, "mismatches": mism}


def main() -> int:
    import json
    print(json.dumps({"active": rung()["id"], "ladder": ladder()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
