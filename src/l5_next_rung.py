"""OWNER 5570456323 / 5570474405 -- the next-rung question in the owner's
units: which single evidence-backed change most increases

    (completed pair edge + credited incentives - residual loss - fees)
    per bankroll-dollar per hour

while preserving L5's sequential behaviour. Levers are scored
independently from the studies on this branch; each carries the evidence,
the expected effect (ESTIMATE, with the standard error that dominates it),
the downside, and whether it changes a behaviour, a safety bound, or only
the environment/execution. Nothing here launches anything.
"""
from __future__ import annotations

import json
import math
import os
from typing import Any, Mapping, Optional, Sequence

A = os.path.join("analysis", "l5")
OUT_PATH = os.path.join(A, "l5_next_rung.json")
BANKROLL_USD = 109.0
R4_CAP_USD = 2.50
R4_SPEND_CAP_USD = 7.50


def _load(name: str) -> dict:
    p = os.path.join(A, name)
    return json.load(open(p)) if os.path.exists(p) else {}


def baseline() -> dict:
    sb = _load("l5_economic_scoreboard.json")
    ou = _load("l5_opportunity_utilization.json")
    rec = _load("l5_capital_recycling.json")
    at = [m for m in sb.get("markets", []) if m.get("cohort") in ("R3L", "R4")]
    nets = [m["net_usd"] for m in at]
    n = len(nets)
    mean = sum(nets) / n if n else None
    se = (math.sqrt(sum((x - mean) ** 2 for x in nets) / (n - 1)) / math.sqrt(n)) if n > 1 else None
    r4 = (sb.get("by_cohort") or {}).get("R4") or {}
    win_h = (ou.get("campaign_window") or {}).get("hours")
    r4_net = r4.get("net_usd")
    return {"at_size_markets": n, "mean_net_per_market": round(mean, 4) if mean is not None else None,
            "se_net_per_market": round(se, 4) if se is not None else None,
            "r4_net_usd": r4_net, "r4_window_hours": win_h,
            "r4_net_per_bankroll_dollar_hour": round(r4_net / (BANKROLL_USD * win_h), 6) if (r4_net is not None and win_h) else None,
            "r4_active_share": (ou.get("opportunity_utilization") or {}).get("active_share"),
            "r4_idle_bottleneck_hours": (ou.get("opportunity_utilization") or {}).get("idle_bottleneck_hours"),
            "r4_markets_per_hour_when_active": round(2 / ((ou.get("hours_by_kind") or {}).get("ACTIVE") or 1), 3) if ou else None,
            "our_turnover_per_capital_hour": (rec.get("ours") or {}).get("overall", {}).get("turnover_per_capital_hour_aggregate"),
            "l5_turnover_per_capital_hour": (rec.get("l5") or {}).get("overall", {}).get("turnover_per_capital_hour_aggregate"),
            "labels": "R4 window from opportunity_utilization; per-market mean/s.e. from the 5 at-size settled markets (venue numbers, derived on flat accounts, labelled)"}


def levers(b: Mapping[str, Any]) -> list:
    mean, se, win_h = b["mean_net_per_market"] or 0.0, b["se_net_per_market"] or 0.0, b["r4_window_hours"] or 1.0
    per_bh = lambda usd: round(usd / (BANKROLL_USD * win_h), 6)
    cap_study = _load("l5_cap_override_study.json")
    curve = {c["cap"]: c for c in (_load("l5_cap_tradeoff_curve.json").get("l5_windows") or {}).get("curve", [])}
    pat = (_load("l5_patience_study.json").get("comparison") or {})
    ou = _load("l5_opportunity_utilization.json")
    foregone = (ou.get("capacity_foregone_in_bottleneck_hours") or {}).get("markets_at_r4_pace_ESTIMATE") or 0.0
    flat_wait_h = (ou.get("hours_by_kind") or {}).get("FLAT_WAIT") or 0.0
    extra_rot = foregone + (flat_wait_h / 0.77 if flat_wait_h else 0.0)
    out = [
        {"lever": "environment selection + rotation: serve the live universe to the selector (recorder pool refresh) and rotate when the account is flat instead of waiting for settlement",
         "changes": "environment / rotation only; no model, cap, size, ceiling or concurrency change",
         "evidence": "opportunity_utilization: 2.51 h of the 4.58 h R4 window idle with 95 executable on-distribution markets on the public gateway and a recorder pool of resolved markets; 0.36 h flat-account wait; L5 re-enters within 60 s of settlement 77% of the time",
         "expected_extra_markets_per_r4_window_ESTIMATE": round(extra_rot, 2),
         "expected_delta_usd_per_window_ESTIMATE": round(extra_rot * mean, 3),
         "expected_delta_per_bankroll_dollar_hour_ESTIMATE": per_bh(extra_rot * mean),
         "uncertainty_1se_usd_per_window": round(extra_rot * se, 3),
         "downside_usd_per_window": round(-extra_rot * R4_CAP_USD, 2),
         "worst_case_bound": "each extra market bounded by the $2.50 unmatched cap and the $7.50 spend cap; daily stop -$15 unchanged",
         "preserves_l5": True,
         "reading": "multiplies the number of L5 sequences per hour by ~%.1fx without touching how any sequence is decided; it also multiplies the sample, which is what the sign of the per-market mean needs" % ((2 + extra_rot) / 2)},
        {"lever": "touch-following reprice inside the 30 s rest window (execution layer)",
         "changes": "execution only; the L5 decision, price band and deadline are unchanged",
         "evidence": "patience study: L5 reprices after a median 6 s (83.5%% inside 30 s), 2 c median step; ours: 9 of 45 orders filled, 23 cancelled untouched at the deadline; fill share %s" % pat.get("our_fill_share"),
         "expected_effect": "raises fills per submitted order; turnover per market and rebates per market scale with fills; the par-band tracker shows 16 completions available and 1 causal -- the lost fills are mostly completions",
         "expected_delta_per_bankroll_dollar_hour_ESTIMATE": None,
         "uncertainty": "no fill-share counterfactual exists yet; measure on the next cohort",
         "downside": "more fills inside the same cap: no new worst case per market; a reprice that chases can pay 1-2 c more per share on completions",
         "preserves_l5": True},
        {"lever": "residual cap $2.50 -> $3.75 (1.5 action units)",
         "changes": "a safety bound (owner parameter)",
         "evidence": "curve: on L5's history +7%% total dollars, income per dollar of peak residual 0.153 vs 0.186; actions kept 39.6%% vs 31.0%%; live: %s of %s EXTEND draws refused at 1 unit" % (
             (cap_study.get("implied_vs_observed") or {}).get("live", {}).get("refusals"), (cap_study.get("implied_vs_observed") or {}).get("live", {}).get("extend_draws")),
         "expected_delta_per_bankroll_dollar_hour_ESTIMATE": per_bh(0.07 * (b["r4_net_usd"] or 0)),
         "downside_usd_per_market": -3.75, "preserves_l5": "improves EXTEND fidelity; still a translation",
         "reading": "buys fidelity, not expectancy, on this history"},
        {"lever": "action size $2.50 -> $5.00 (10 shares), cap held at $2.50",
         "changes": "size (owner parameter); the cap falls to 0.5 units",
         "evidence": "ramp: L5's early day-median action was $3.25; its mature 10-13 share median came later",
         "expected_delta_per_bankroll_dollar_hour_ESTIMATE": per_bh((b["r4_net_usd"] or 0)),
         "uncertainty": "doubles the standard error with the mean; refuses more EXTENDs (fidelity falls below 31%)",
         "downside_usd_per_market": -2.50, "preserves_l5": "worsens EXTEND fidelity unless the cap moves with it"},
        {"lever": "concurrency 1 -> 2", "changes": "capital at risk x2", "evidence": "L5 never ran two windows of a lane at once",
         "expected_delta_per_bankroll_dollar_hour_ESTIMATE": per_bh(mean * 2), "downside_usd": -5.0, "preserves_l5": False,
         "reading": "last, by the owner's order"},
    ]
    return out


def answer(b: Mapping[str, Any], lv: Sequence[dict]) -> dict:
    first = lv[0]
    return {"single_change_to_test_after_r4": first["lever"],
            "why": ("it is the only lever that raises L5 sequences per bankroll-dollar-hour without changing what an L5 sequence is; "
                    "its expected dollar effect is the per-market mean (%s, s.e. %s) times ~%s extra markets per R4-length window, "
                    "so its sign is the sign of the mean -- which more sequences are also the fastest way to establish; downside per "
                    "extra market is the existing $2.50 cap." % (b["mean_net_per_market"], b["se_net_per_market"], first["expected_extra_markets_per_r4_window_ESTIMATE"])),
            "second": lv[1]["lever"],
            "not_now": [x["lever"] for x in lv[2:]],
            "conditions": ["R4 completes its 3-market cohort untouched", "owner approval of the change and of a fresh rung identity",
                           "everything else frozen: model, cap $2.50, ceiling 1.00, size $2.50, concurrency 1, maker-only, deadman"],
            "labels": "an answer for the owner's decision, not a promotion; every expected value is an ESTIMATE whose sign is not established"}


def study() -> dict:
    b = baseline()
    lv = levers(b)
    return {"owner_direction": "#167 5570456323 (next-rung question), 5570474405 (invariant)",
            "invariant": "L5 DECIDES -> EXECUTE WELL -> RECYCLE WHEN L5 COMPLETES -> MEASURE TOTAL ECONOMICS -> SCALE WHAT WORKS",
            "objective": "(completed pair edge + credited incentives - residual loss - fees) per bankroll-dollar per hour, L5 behaviour preserved",
            "baseline": b, "levers": lv, "answer": answer(b, lv),
            "labels": "read-only; ESTIMATES; no rung is launched by this file"}


def render_md(o: Mapping[str, Any]) -> str:
    b = o["baseline"]
    L = ["# Next rung (OWNER 5570456323 / 5570474405)\n", "**Invariant:** %s\n" % o["invariant"], "**Objective:** %s\n" % o["objective"],
         "Baseline (R4 window %s h): net $%s = %s per bankroll-dollar-hour; active share %s; per-market mean $%s, s.e. $%s on %s at-size markets; turnover per capital-hour ours %s vs L5 %s.\n"
         % (b["r4_window_hours"], b["r4_net_usd"], b["r4_net_per_bankroll_dollar_hour"], b["r4_active_share"], b["mean_net_per_market"], b["se_net_per_market"], b["at_size_markets"],
            b["our_turnover_per_capital_hour"], b["l5_turnover_per_capital_hour"]),
         "| lever | changes | expected delta per bankroll-$-hour (EST) | downside | preserves L5 | evidence |", "|---|---|---|---|---|---|"]
    for lv in o["levers"]:
        L.append("| %s | %s | %s | %s | %s | %s |" % (lv["lever"], lv["changes"], lv.get("expected_delta_per_bankroll_dollar_hour_ESTIMATE"),
                                                   lv.get("downside_usd_per_window", lv.get("downside_usd_per_market", lv.get("downside_usd", lv.get("downside")))),
                                                   lv["preserves_l5"], str(lv["evidence"]).replace("|", "/")))
    a = o["answer"]
    L.append("\n**Answer:** %s. %s\n\nSecond: %s. Not now: %s.\n\nConditions: %s\n\n%s\n" % (a["single_change_to_test_after_r4"], a["why"], a["second"], "; ".join(a["not_now"]), "; ".join(a["conditions"]), a["labels"]))
    return "\n".join(L)


def main(argv: Optional[Sequence[str]] = None) -> int:
    out = study()
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, sort_keys=True, default=str)
    with open(os.path.join(A, "L5_NEXT_RUNG.md"), "w", encoding="utf-8") as f:
        f.write(render_md(out))
    print(render_md(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
