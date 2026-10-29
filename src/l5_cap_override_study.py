"""Why R4's dollar residual cap overrides L5 EXTEND draws -- measured on L5's
own history, then set against the live refusals.

OWNER (chat, 2026-09-07 ~11:00Z): "analyze why our $2.50 residual cap has
already overridden [L5] EXTEND decisions". By 09:25Z the count was 44.

THE MECHANISM, stated so it can be checked against the numbers below.
R4 sizes every action at ~$2.50 (per_action_target_usd) and caps unmatched
exposure at $2.50 (max_unmatched_usd). The cap is therefore ONE ACTION'S
NOTIONAL. An EXTEND adds an action's notional to an already-unmatched
residual, so any EXTEND drawn while unmatched exposure is above ~$0 pushes
the preview over the cap and is refused. Live decisions 38-41 (market 1):
unmatched_before 2.447 -> after-if-filled 4.84-4.93 > 2.50, refused. Live
decisions 13-17 (market 2): unmatched_before 0.0517 -> after 2.54-2.61 >
2.50, refused -- a residual of five CENTS was enough, because one action
alone nearly fills the cap.

L5 does not size like that. This module measures, per L5 window, the
window's own "action unit" (its median BUY notional) and, for every EXTEND
fill L5 made, the unmatched exposure it CREATED in those units. A cap of k
action units refuses an EXTEND iff unmatched_after > k units. k = 1 is R4.
The share of L5's EXTENDs refused at k = 1, 1.5, 2, 3, 4 is the answer to
"why so many overrides", and the k at which the refusal share becomes
small is what a cap sized to L5's envelope would look like -- reported,
NOT applied: OWNER 5567269295 "If the $2.50 residual cap itself blocks
L5-consistent actions, classify that separately rather than changing it
mid-cohort."

RELATIONS are derived from the canonical rows' own inventory columns (no
model involved): a BUY from flat is OPEN; a BUY on the residual side is
EXTEND; a BUY on the other side is COMPLETE. The 2 SELL rows are skipped.

PROVENANCE / LIMITS: analysis/l5/l5_canonical_sequence.csv.gz, executed
trades only; quotes and cancels are invisible, so "L5 extended" means a
fill arrived, and nothing here says what L5 tried and could not fill.
Nothing here is a P&L claim.
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Optional, Sequence

from src.l5_concurrency_study import CANONICAL_PATH, load_rows, provenance
from src.l5_residual_risk import (NOTIONAL_TOL, _era, _f, _lane, _size_band,
                                  dist, residual_at)

OUT_PATH = os.path.join("analysis", "l5", "l5_cap_override_study.json")
LIVE_DECISIONS_PATH = os.path.join("analysis", "l5", "r4_decisions.jsonl")
CAP_UNITS = (1.0, 1.5, 2.0, 3.0, 4.0)
R4_CAP_USD = 2.50
R4_ACTION_USD = 2.50


def relation_of(prev_residual_signed: float, row: dict) -> Optional[str]:
    """OPEN / EXTEND / COMPLETE from the inventory columns alone."""
    if str(row.get("side") or "").upper() != "BUY":
        return None
    outcome = str(row.get("outcome") or "")
    if abs(prev_residual_signed) <= 1e-9:
        return "open"
    residual_side = "Up" if prev_residual_signed > 0 else "Down"
    return "extend" if outcome == residual_side else "complete"


def window_actions(rows: Sequence[dict]) -> List[dict]:
    """Every BUY fill of one window with its relation, the unmatched
    exposure before and after, and its notional."""
    rs = sorted(rows, key=lambda r: (int(r["timestamp"]), int(r["trade_seq"])))
    out: List[dict] = []
    prev_signed = 0.0
    prev_notional = 0.0
    for r in rs:
        rel = relation_of(prev_signed, r)
        after = residual_at(r)
        if rel is not None:
            out.append({
                "relation": rel,
                "timestamp": int(_f(r.get("timestamp"))),
                "seconds_to_settlement": int(_f(r.get("seconds_to_settlement"))),
                "notional": round(_f(r.get("dollars")), 6),
                "price": round(_f(r.get("price")), 6),
                "unmatched_before": round(prev_notional, 6),
                "unmatched_after": after["notional"],
            })
        prev_signed = _f(r.get("residual_qty_signed"))
        prev_notional = after["notional"]
    return out


def window_study(rows: Sequence[dict]) -> dict:
    acts = window_actions(rows)
    last = sorted(rows, key=lambda r: (int(r["timestamp"]), int(r["trade_seq"])))[-1]
    buys = [a["notional"] for a in acts]
    unit = _median(buys) if buys else 0.0
    extends = [a for a in acts if a["relation"] == "extend"]
    for a in acts:
        a["units_after"] = round(a["unmatched_after"] / unit, 4) if unit else None
        a["units_before"] = round(a["unmatched_before"] / unit, 4) if unit else None
    refused = {}
    for k in CAP_UNITS:
        refused[str(k)] = sum(1 for a in extends
                              if a["units_after"] is not None and a["units_after"] > k)
    peak_units = max((a["units_after"] for a in acts if a["units_after"] is not None),
                     default=0.0)
    # the behaviour the cap forbids: after an EXTEND beyond one unit, did a
    # complementary fill bring exposure back under one unit before settlement?
    reduced_back = None
    over = [i for i, a in enumerate(acts)
            if a["relation"] == "extend" and (a["units_after"] or 0) > 1.0]
    if over:
        i0 = over[0]
        reduced_back = any((a["units_after"] or 0) <= 1.0 for a in acts[i0 + 1:])
    return {
        "window_id": last["window_id"], "market_slug": last["market_slug"],
        "asset": last.get("asset", ""),
        "duration_minutes": int(_f(last.get("duration_minutes"))),
        "settlement_ts": int(_f(last.get("timestamp"))) + int(_f(last.get("seconds_to_settlement"))),
        "window_spend_usd": round(sum(buys), 6),
        "action_unit_usd": round(unit, 6),
        "actions": len(acts),
        "opens": sum(1 for a in acts if a["relation"] == "open"),
        "extends": len(extends),
        "completes": sum(1 for a in acts if a["relation"] == "complete"),
        "peak_units": round(peak_units, 4),
        "extends_refused_at": refused,
        "extended_beyond_one_unit": bool(over),
        "reduced_back_under_one_unit_before_settlement": reduced_back,
        "extend_units_after": [a["units_after"] for a in extends
                               if a["units_after"] is not None],
        "extend_units_before": [a["units_before"] for a in extends
                                if a["units_before"] is not None],
    }


def _median(xs: Sequence[float]) -> float:
    ys = sorted(xs)
    n = len(ys)
    if not n:
        return 0.0
    return ys[n // 2] if n % 2 else 0.5 * (ys[n // 2 - 1] + ys[n // 2])


def windows(rows: Iterable[dict]) -> List[dict]:
    by_win: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        by_win[r["window_id"]].append(r)
    out = [window_study(rs) for rs in by_win.values()]
    out.sort(key=lambda w: w["settlement_ts"])
    return out


def summary(ws: Sequence[dict]) -> dict:
    actions = sum(w["actions"] for w in ws)
    extends = sum(w["extends"] for w in ws)
    units_after = [u for w in ws for u in w["extend_units_after"]]
    units_before = [u for w in ws for u in w["extend_units_before"]]
    refused = {k: sum(w["extends_refused_at"][k] for w in ws) for k in
               (str(x) for x in CAP_UNITS)}
    over = [w for w in ws if w["extended_beyond_one_unit"]]
    reduced = [w for w in over if w["reduced_back_under_one_unit_before_settlement"]]
    return {
        "windows": len(ws),
        "actions": actions,
        "relation_mix": {
            "open": sum(w["opens"] for w in ws),
            "extend": extends,
            "complete": sum(w["completes"] for w in ws),
            "extend_share_of_actions": round(extends / actions, 4) if actions else None,
        },
        "action_unit_usd": dist([w["action_unit_usd"] for w in ws], 2),
        "extend_unmatched_after_in_action_units": dist(units_after),
        "extend_unmatched_before_in_action_units": dist(units_before),
        "extends_refused_by_cap_in_action_units": {
            k: {"refused": n,
                "share_of_extends": round(n / extends, 4) if extends else None}
            for k, n in refused.items()},
        "windows_with_an_extend_beyond_one_unit": len(over),
        "share_of_windows_extending_beyond_one_unit": (
            round(len(over) / len(ws), 4) if ws else None),
        "of_those_reduced_back_under_one_unit_before_settlement": {
            "windows": len(reduced),
            "share": round(len(reduced) / len(over), 4) if over else None,
        },
        "peak_units_per_window": dist([w["peak_units"] for w in ws]),
    }


def by(ws: Sequence[dict], key) -> dict:
    groups: Dict[str, List[dict]] = defaultdict(list)
    for w in ws:
        groups[key(w)].append(w)
    return {k: summary(v) for k, v in sorted(groups.items())}


# ---- the live side: R4 refusals from the harvested decision rows ----

def load_live_decisions(path: str = LIVE_DECISIONS_PATH) -> List[dict]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def live_refusals(decisions: Sequence[dict], cap_usd: float = R4_CAP_USD,
                  action_usd: float = R4_ACTION_USD) -> dict:
    """The refusals the cap actually produced, in the same units."""
    refused = [d for d in decisions
               if str(d.get("refusal") or "").startswith("unmatched_usd_cap")]
    by_state: Counter = Counter(d.get("state") for d in refused)
    by_market: Counter = Counter(d.get("slug") for d in refused)
    by_draw: Counter = Counter(d.get("would_act_without_safety_cap") for d in refused)
    before = [_f(d.get("unmatched_usd_before")) for d in refused]
    after = [_f(d.get("unmatched_usd_after_if_filled")) for d in refused]
    scored = [d for d in decisions if d.get("state")]
    extend_draws = [d for d in scored
                    if (d.get("would_act_without_safety_cap") or d.get("acted")) == "extend"]
    return {
        "source": LIVE_DECISIONS_PATH,
        "decisions": len(scored),
        "cap_usd": cap_usd, "action_usd": action_usd,
        "cap_in_action_units": round(cap_usd / action_usd, 4) if action_usd else None,
        "refusals": len(refused),
        "refusal_share_of_decisions": round(len(refused) / len(scored), 4) if scored else None,
        "extend_draws": len(extend_draws),
        "extend_draws_refused_share": (
            round(len(refused) / len(extend_draws), 4) if extend_draws else None),
        "by_draw": dict(by_draw),
        "by_state": dict(by_state.most_common()),
        "by_market": dict(by_market.most_common()),
        "unmatched_before_usd": dist(before),
        "unmatched_after_if_filled_usd": dist(after),
        "unmatched_after_in_action_units": dist([a / action_usd for a in after]),
        "reading": ("every refusal is an EXTEND draw whose after-if-filled "
                    "exposure exceeded one action's notional; a residual of a "
                    "few cents is enough because the cap equals one action"),
    }


def study(rows: Optional[Sequence[dict]] = None, path: str = CANONICAL_PATH,
          live_path: str = LIVE_DECISIONS_PATH) -> dict:
    rows = list(rows) if rows is not None else load_rows(path)
    ws = windows(rows)
    prov = provenance(rows)
    prov["limits"] = list(prov["limits"]) + [
        "relations derived from the canonical inventory columns: BUY from flat "
        "= open; BUY on the residual side = extend; BUY on the other side = "
        "complete; SELL rows skipped",
        "action unit = the window's own median BUY notional; a cap of k units "
        "refuses an EXTEND iff unmatched exposure after it exceeds k units",
        "fills only: what L5 tried and could not fill is invisible",
    ]
    overall = summary(ws)
    live = live_refusals(load_live_decisions(live_path))
    k1 = overall["extends_refused_by_cap_in_action_units"]["1.0"]["share_of_extends"]
    p_extend = overall["relation_mix"]["extend_share_of_actions"]
    return {
        "owner_direction": "chat 2026-09-07 ~11:00Z: analyze why the $2.50 "
                           "residual cap has overridden L5 EXTEND decisions",
        "provenance": prov,
        "mechanism": {
            "r4_cap_usd": R4_CAP_USD, "r4_action_usd": R4_ACTION_USD,
            "cap_in_action_units": R4_CAP_USD / R4_ACTION_USD,
            "statement": "the cap equals one action's notional, so any EXTEND "
                         "drawn with unmatched exposure above ~0 is refused",
        },
        "overall": overall,
        "by_lane": by(ws, _lane),
        "by_era": by(ws, _era),
        "by_size_band": by(ws, _size_band),
        "implied_vs_observed": {
            "l5_extend_share_of_actions": p_extend,
            "l5_extends_refused_share_at_k1": k1,
            "implied_refusal_share_of_l5_actions_at_k1": (
                round(p_extend * k1, 4) if (p_extend is not None and k1 is not None) else None),
            "live": live,
        },
        "classification": ("safety-cap override (R4 dollar residual cap): the "
                           "refusals are L5-consistent actions blocked by the "
                           "cap, reported separately per OWNER 5567269295; no "
                           "cap change is made here"),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT_PATH)
    ap.add_argument("--canonical", default=CANONICAL_PATH)
    ap.add_argument("--live", default=LIVE_DECISIONS_PATH)
    a = ap.parse_args(argv)
    out = study(path=a.canonical, live_path=a.live)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, sort_keys=True)
    o = out["overall"]
    print(json.dumps({
        "windows": o["windows"], "extends": o["relation_mix"]["extend"],
        "extend_share": o["relation_mix"]["extend_share_of_actions"],
        "refused_share_by_k": {k: v["share_of_extends"]
                               for k, v in o["extends_refused_by_cap_in_action_units"].items()},
        "live_refusals": out["implied_vs_observed"]["live"]["refusals"],
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
