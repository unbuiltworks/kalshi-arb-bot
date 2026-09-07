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
    # was "R4"; renamed when the owner approved the REAL R4 (5567269295) so
    # the two can never share an id. This prebuilt rung never ran and is
    # reachable only through the inert automatic ladder (next_rung/evaluate).
    {"id": "R4-AUTO", "name": "size: $0.10 per market (prebuilt, never run)",
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
    # OWNER live decision 2026-09-06 (uptime): R2C's caps EXACTLY, with
    # concurrency 2. Measured cause: under concurrency 1 the campaign is
    # idle whenever one market is held to settlement -- 19:51Z to 21:28Z
    # (97 min) on one college-football total, then held again minutes
    # later. L5's own engine never had this problem because its windows
    # settled every 5-15 minutes; multi-hour sports markets make the
    # hold-to-settlement rule a throughput ceiling, not a risk control.
    # Nothing about the money limits changes except the number of markets
    # that may be OPEN at once: same $0.15 per market, same residual 6,
    # same -$1.00 daily stop; only the CONCURRENT exposure
    # moves, $0.15 -> $0.30. One session at a time, one outstanding
    # order, maker-only, fee-safe 0.98 and HOLD-to-settlement are all
    # untouched: the campaign works its held markets round-robin.
    # OWNER live 2026-09-07 ("keep going, let's go live"): shortened from 6
    # markets to 3. The shell is already PROVEN in production -- 23:59-00:22Z
    # the campaign held two markets and worked them round-robin, one session
    # at a time, with no concurrency_breach -- so the remaining three markets
    # would only delay the owner-approved SIZED rung behind it. Strictly less
    # exposure: worst case per campaign $0.90 -> $0.45.
    {"id": "R2C2", "name": "R2C caps, concurrency 2 (OWNER live 2026-09-06)",
     "per_market_cap_usd": "0.15", "residual_units": 6, "markets_per_campaign": 3,
     "campaigns_per_day": 1, "max_actions_per_session": 6, "concurrency": 2,
     "lever": "concurrency", "promote": None,
     "derived_from": "R2C cohort 2026-09-06: 97 minutes idle on one held "
                     "market, 3 of 6 markets filled in 2 hours"},
    # OWNER 5561465636 (LIVE AUTHORIZATION, $110 account): the first
    # L5-SIZED rung, derived from L5's own per-action distribution
    # (analysis/l5/l5_canonical_sequence.csv.gz, 55,647 maker actions:
    # shares p10/p25/p50 = 2 / 5 / 10.7, dollars p25/p50 = $0.94 / $3.73;
    # window spend p25/p50 = $32 / $93; final |residual| p50 = 55 shares)
    # and the venue's published maker-rebate arithmetic (0.0125 x C x
    # p(1-p), rounded to the cent): the smallest per-action size that is
    # inside L5's own size distribution (5 shares = L5's maker p25) AND
    # produces a non-rounded rebate at every price in the 35-65 band
    # (5 x 0.0125 x 0.2275..0.25 = $0.014-0.016 -> $0.01-0.02 per fill,
    # vs $0.000062 -> $0.00 at the R1/R2C 0.02 shares). $2.50 target at
    # the entry price (5.0 shares at 0.50, 5.55 at 0.45, 3.85 at 0.65),
    # either leg <= $3.25. Per-market cap $7.50 = three first legs (23% of
    # L5's p25 window spend, the honest bound), residual cap 6 units
    # (~30 shares, 55% of L5's median final residual), 3 markets per
    # campaign, 1 campaign per day, concurrency 1, 6 actions per session.
    # Worst case $7.50 per market / $22.50 per campaign (20.5% of the
    # $110 account; needs three consecutive markets to lose every first
    # leg with no pair formed); the rung's own daily realized-loss stop
    # is -$15.00 (13.6%), checked before every session, so the per-day
    # bound is the stop plus at most the one in-flight market. Ends at
    # CAMPAIGN_RUNG_COMPLETE for the owner: no automatic promotion and
    # no automatic repeat.
    {"id": "R3L", "name": "L5 size: $2.50 per action (~5 shares), $7.50 per market, "
                          "residual 6 units, 3 markets (OWNER 5561465636)",
     "per_market_cap_usd": "7.50", "residual_units": 6, "markets_per_campaign": 3,
     # OWNER DECISION 5564173746 (2026-09-07T02:23:51Z): "Keep execution
     # concurrency = 1 for R2C/R3L." One market at a time, its full L5
     # inventory sequence run to terminal, then rotate to the next best.
     # This REVERSES the concurrency 2 I applied at 01:00Z on the earlier
     # chat decision: replicating L5 comes first, and concurrency >1 is to
     # be proposed only when L5's OWN history supports it, the bankroll
     # supports it, and R3L has shown clean sized fills -- never to
     # manufacture throughput. Max concurrent exposure returns to $7.50.
     "campaigns_per_day": 1, "max_actions_per_session": 6, "concurrency": 1,
     "target_notional_usd": "2.50", "max_leg_notional_usd": "3.25",
     "daily_loss_stop_usd": "-15.00", "lever": "size", "promote": None,
     "derived_from": "L5 canonical maker actions (shares p25 = 5.0) + published "
                     "rebate rounding (>= 1.62 shares for one cent at 0.45); "
                     "R1/R2C cohorts 2026-09-06"},
    # OWNER DECISION 5567269295 (2026-09-07T07:53:37Z): "APPROVE R4, with one
    # accounting/economic guardrail." Built around the demonstrated transfer
    # mismatch -- residual DURATION in sports -- and nothing else:
    #   * size UNCHANGED from R3L ($2.50 per action, either leg <= $3.25,
    #     $7.50 per market, 3 markets, concurrency 1, -$15.00 daily stop);
    #   * the residual cap becomes DOLLARS: at most $2.50 of UNMATCHED
    #     exposure per market (unmatched shares x their own average cost,
    #     the amount lost if that side expires worthless). Anchored to L5's
    #     own envelope (analysis/l5/l5_residual_risk.json): at material size
    #     L5 held peak unmatched to 10.8-13.7% of deployed capital, which at
    #     our $7.50 cap is $1.03 -- below one action -- so one action is the
    #     tightest implementable cap. The unit cap (6) is kept as the
    #     behavioral EXTEND bound; the dollar cap binds first;
    #   * the completion bound rises 0.98 -> 1.00 for COMPLETE (deficit-side)
    #     orders only: the MEASURED R3L rebate ($0.2800 on $34.6402 filled,
    #     0.808%) pays for a pair up to ~1.003, and the owner's guardrail is
    #     that 1.00 is a HARD ceiling -- "do not operationally spend rebate
    #     beyond par" until R4's own credited rebates are in hand. The ">=1c
    #     after known fees" rule still governs OPEN/EXTEND; par is permitted
    #     only where it REDUCES unmatched exposure.
    # Campaign worst case in dollars AT RISK: 3 x $2.50 = $7.50 (6.9% of the
    # $108.9985 balance at approval); capital DEPLOYED is still bounded by
    # 3 x $7.50 = $22.50 and the sized-rung preflight keeps requiring it.
    # Ends at CAMPAIGN_RUNG_COMPLETE; "No automatic R5 promotion."
    {"id": "R4", "name": "L5 size, residual in DOLLARS: $2.50 unmatched cap, "
                         "completion to par (OWNER 5567269295)",
     "per_market_cap_usd": "7.50", "residual_units": 6, "markets_per_campaign": 3,
     "max_unmatched_usd": "2.50", "pair_cost_max": "1.00",
     "campaigns_per_day": 1, "max_actions_per_session": 6, "concurrency": 1,
     "target_notional_usd": "2.50", "max_leg_notional_usd": "3.25",
     "daily_loss_stop_usd": "-15.00", "lever": "residual_duration", "promote": None,
     "derived_from": "R3L (19 fills, -$1.5302 net, reconciled to the cent); L5 "
                     "residual envelope 2,216 windows (peak unmatched / spend "
                     "0.108-0.137 at material size); measured maker rebate "
                     "0.808% of filled dollars"},
)
ALL_RUNGS: tuple[dict, ...] = RUNGS + OWNER_RUNGS
# fail closed at import: rung() returns the FIRST id match, so a duplicate id
# would silently run one rung's money limits under another rung's name
assert len({r["id"] for r in ALL_RUNGS}) == len(ALL_RUNGS), \
    "duplicate rung id in the ramp ladder"
DEFAULT_PAIR_COST_MAX = D("0.98")      # the fee-safe observation cap, R1..R3L
DEFAULT_RESIDUAL_UNITS = 3
DEFAULT_TARGET_NOTIONAL = D("0.01")     # R1..R2C per-action target (micro)
DEFAULT_MAX_LEG_NOTIONAL = D("0.02")    # R1..R2C either-leg bound (micro)


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
    if r.get("max_unmatched_usd"):
        # OWNER 5567269295: a dollar-residual rung names BOTH levers it
        # changed -- the unmatched cap and the completion ceiling -- so the
        # frozen evaluator splits this cohort from every fee-safe0.98 one
        label = (f"{r['id']}/cap{r['per_market_cap_usd']}"
                 f"/unmatched{r['max_unmatched_usd']}"
                 f"/complete{r.get('pair_cost_max') or DEFAULT_PAIR_COST_MAX}"
                 f"/conc{r['concurrency']}/maker-only/rest30s")
    else:
        label = (f"{r['id']}/cap{r['per_market_cap_usd']}"
                 f"/residual{int(r.get('residual_units') or DEFAULT_RESIDUAL_UNITS)}"
                 f"/conc{r['concurrency']}/maker-only/fee-safe0.98/rest30s")
    if r.get("target_notional_usd"):
        # OWNER 5561465636: a sized rung carries its per-action size in
        # the label; the R1 / R2C labels are unchanged
        label += f"/action{r['target_notional_usd']}"
    return label


def max_unmatched_usd(r: Optional[Mapping[str, Any]] = None) -> Optional[D]:
    """The ACTIVE rung's cap on UNMATCHED dollars per market (OWNER
    5567269295), or None for a rung that caps residual in units only."""
    r = dict(r) if r is not None else active()
    v = r.get("max_unmatched_usd")
    return D(str(v)) if v else None


def pair_cost_max(r: Optional[Mapping[str, Any]] = None) -> D:
    """The ACTIVE rung's HARD ceiling on a completed pair's cost: 0.98
    (fee-safe observation cap) unless the owner's rung states its own.
    Never read above 1.00 -- par is the owner's guardrail, whatever a
    rung says."""
    r = dict(r) if r is not None else active()
    v = D(str(r.get("pair_cost_max") or DEFAULT_PAIR_COST_MAX))
    return min(v, D("1.00"))


def target_notional(r: Optional[Mapping[str, Any]] = None) -> D:
    """Per-action maker target notional of the ACTIVE rung (OWNER
    5561465636 size lever); rungs without one keep the micro default."""
    r = dict(r) if r is not None else active()
    return D(str(r.get("target_notional_usd") or DEFAULT_TARGET_NOTIONAL))


def max_leg_notional(r: Optional[Mapping[str, Any]] = None) -> D:
    r = dict(r) if r is not None else active()
    return D(str(r.get("max_leg_notional_usd") or DEFAULT_MAX_LEG_NOTIONAL))


def daily_loss_stop(r: Optional[Mapping[str, Any]] = None) -> D:
    """The daily realized-loss stop the ACTIVE rung runs under: the
    absolute -$1.00 unless the owner's rung states its own, scaled to
    its worst case (OWNER 5560851070 / 5561465636)."""
    r = dict(r) if r is not None else active()
    return D(str(r.get("daily_loss_stop_usd") or DAILY_LOSS_STOP))


def next_rung(rung_id: str) -> Optional[dict]:
    ids = [r["id"] for r in RUNGS]
    i = ids.index(rung_id) if rung_id in ids else 0
    return dict(RUNGS[i + 1]) if i + 1 < len(RUNGS) else None


def worst_case(r: Mapping[str, Any]) -> dict:
    cap = D(str(r["per_market_cap_usd"]))
    per_campaign = cap * r["markets_per_campaign"]
    per_day_raw = per_campaign * r["campaigns_per_day"]
    concurrent = cap * r["concurrency"]
    stop = daily_loss_stop(r)
    # the stop is checked before every session, so the one in-flight
    # market can still lose its cap after the stop is reached
    per_day = min(per_day_raw, -stop + cap)
    out = {"per_market_usd": str(cap), "per_campaign_usd": str(per_campaign),
           "per_day_uncapped_usd": str(per_day_raw),
           "per_day_bounded_by_daily_stop_usd": str(per_day),
           "max_concurrent_open_exposure_usd": str(concurrent),
           "daily_loss_stop_usd": str(stop)}
    if r.get("target_notional_usd"):
        out["per_action_target_usd"] = str(r["target_notional_usd"])
        out["per_leg_max_usd"] = str(r.get("max_leg_notional_usd"))
    if r.get("max_unmatched_usd"):
        # OWNER 5567269295: capital DEPLOYED (per_market / per_campaign
        # above) is still bounded by the spend cap; the dollars actually AT
        # RISK are the unmatched ones -- a matched pair pays $1 whatever
        # happens -- so both are reported and never conflated
        at_risk = D(str(r["max_unmatched_usd"]))
        out["max_at_risk_per_market_usd"] = str(at_risk)
        out["per_campaign_at_risk_usd"] = str(at_risk * r["markets_per_campaign"])
        out["pair_cost_max"] = str(pair_cost_max(r))
    return out


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
