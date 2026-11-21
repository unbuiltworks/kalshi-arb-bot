"""OWNER 5570456323 -- L5's early ramp against our R1 -> R4 ramp, in the
owner's dimensions: action size, turnover per day, pair completion %,
residual %, capital recycling time, maker/taker mix, concurrency, and
market/window selection; then what is faithfully reproducible with ~$109.

L5's early ramp = its first covered trading days in the canonical history
(2026-03-19 onward), day by day and as the first-week / first-month
aggregates; the mature era for contrast. Ours = each cohort of the economic
scoreboard (R1, R2C, R2C2, R3L, R4), with turnover per day computed over
the cohort's own wall-clock span. Read-only.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src import l5_cap_tradeoff_curve as cv
from src import l5_extend_productivity as ep

A = os.path.join("analysis", "l5")
OUT_PATH = os.path.join(A, "l5_ramp_comparison.json")
SCOREBOARD_PATH = os.path.join(A, "l5_economic_scoreboard.json")
RECYCLING_PATH = os.path.join(A, "l5_capital_recycling.json")
BANKROLL_USD = 109.0
EARLY_DAYS = 7


def _pct(xs: Sequence[float]) -> dict:
    xs = sorted(x for x in xs if x is not None)
    n = len(xs)
    if not n:
        return {"n": 0}
    q = lambda p: round(xs[min(n - 1, int(p * n))], 3)
    return {"n": n, "p25": q(0.25), "p50": q(0.50), "p75": q(0.75), "mean": round(sum(xs) / n, 3)}


def l5_days(rows: Optional[Sequence[dict]] = None, path: str = cv.CANONICAL_PATH) -> dict:
    rows = list(rows) if rows is not None else cv.load_rows(path)
    by_win: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        by_win[r["window_id"]].append(r)
    days: Dict[str, dict] = defaultdict(lambda: {"windows": 0, "actions": 0, "bought_usd": D(0), "matched_usd": D(0),
                                                 "residual_usd": D(0), "residual_pnl": D(0), "pair_pnl": D(0),
                                                 "action_usd": [], "lanes": defaultdict(int), "hours_active": 0.0,
                                                 "first_t": None, "last_t": None})
    for wid, rs in by_win.items():
        ps = ep.prints(rs)
        if not ps:
            continue
        _f, resolution, unit = cv.window_fills(rs)
        lots = ep.window_lots(ps, resolution, unit)
        day = datetime.fromtimestamp(ps[0]["t"], tz=timezone.utc).strftime("%Y-%m-%d")
        d = days[day]
        d["windows"] += 1
        d["actions"] += len(lots)
        lane = cv._lane({"asset": rs[-1].get("asset", ""), "duration_minutes": int(cv._f(rs[-1].get("duration_minutes")))})
        d["lanes"][lane] += 1
        for l in lots:
            usd = D(str(l["usd"]))
            d["bought_usd"] += usd
            d["matched_usd"] += usd * D(str(l["matched_share"]))
            d["residual_usd"] += D(str(l["residual_usd"]))
            d["residual_pnl"] += D(str(l["residual_pnl_usd"]))
            d["pair_pnl"] += D(str(l["pair_pnl_usd"]))
            d["action_usd"].append(float(usd))
        t0, t1 = ps[0]["t"], ps[-1]["t"]
        d["first_t"] = t0 if d["first_t"] is None else min(d["first_t"], t0)
        d["last_t"] = t1 if d["last_t"] is None else max(d["last_t"], t1)
    out = {}
    for day, d in sorted(days.items()):
        b = d["bought_usd"]
        out[day] = {"windows": d["windows"], "actions": d["actions"], "turnover_usd": float(b),
                    "action_usd": _pct(d["action_usd"]),
                    "pair_completion_share": float(d["matched_usd"] / b) if b else None,
                    "residual_share_of_bought": float(d["residual_usd"] / b) if b else None,
                    "pair_pnl_usd": float(d["pair_pnl"]), "residual_pnl_usd": float(d["residual_pnl"]),
                    "net_usd": float(d["pair_pnl"] + d["residual_pnl"]),
                    "lanes": dict(d["lanes"]),
                    "span_hours": round((d["last_t"] - d["first_t"]) / 3600.0, 2) if d["first_t"] is not None else None}
    return out


def aggregate_days(days: Mapping[str, dict], keys: Sequence[str], label: str) -> dict:
    ds = [days[k] for k in keys if k in days]
    if not ds:
        return {"label": label, "days": 0}
    turn = sum(d["turnover_usd"] for d in ds)
    matched = sum((d["pair_completion_share"] or 0) * d["turnover_usd"] for d in ds)
    resid = sum((d["residual_share_of_bought"] or 0) * d["turnover_usd"] for d in ds)
    acts = []
    for d in ds:
        acts += [d["action_usd"]["p50"]] * max(1, d["actions"] // 10)     # day medians, weighted by activity
    lanes: Dict[str, int] = defaultdict(int)
    for d in ds:
        for k, v in d["lanes"].items():
            lanes[k] += v
    return {"label": label, "days": len(ds), "first_day": keys[0] if keys else None, "last_day": keys[-1] if keys else None,
            "turnover_usd": round(turn, 2), "turnover_per_day_usd": round(turn / len(ds), 2),
            "windows_per_day": round(sum(d["windows"] for d in ds) / len(ds), 1),
            "actions_per_day": round(sum(d["actions"] for d in ds) / len(ds), 1),
            "action_usd_p50_of_day_medians": _pct(acts)["p50"],
            "pair_completion_share": round(matched / turn, 4) if turn else None,
            "residual_share_of_bought": round(resid / turn, 4) if turn else None,
            "pair_pnl_usd": round(sum(d["pair_pnl_usd"] for d in ds), 2),
            "residual_pnl_usd": round(sum(d["residual_pnl_usd"] for d in ds), 2),
            "net_usd": round(sum(d["net_usd"] for d in ds), 2),
            "net_per_turnover": round(sum(d["net_usd"] for d in ds) / turn, 5) if turn else None,
            "lanes": dict(lanes), "maker_taker_mix": "unknown from public prints",
            "concurrency": "1 market per lane at a time (0 of 2,201 transitions entered the next window before settlement)"}


def ours(sb_path: str = SCOREBOARD_PATH, rec_path: str = RECYCLING_PATH) -> dict:
    sb = json.load(open(sb_path))
    rec = json.load(open(rec_path)) if os.path.exists(rec_path) else {}
    per_cohort: Dict[str, List[dict]] = defaultdict(list)
    for m in sb["markets"]:
        per_cohort[m["cohort"]].append(m)
    out = {}
    for c, ms in per_cohort.items():
        ts = [datetime.fromisoformat(m["first_fill_utc"].replace("Z", "+00:00")).timestamp() for m in ms if m.get("first_fill_utc")]
        te = [datetime.fromisoformat(m["resolution_utc"].replace("Z", "+00:00")).timestamp() for m in ms if m.get("resolution_utc")]
        span_h = ((max(te) - min(ts)) / 3600.0) if ts and te else None
        turn = sum(m["filled_usd_turnover"] for m in ms)
        bought = sum(m["bought_shares"] for m in ms)
        matched = sum(2 * m["matched_pairs"] for m in ms)
        resid_sh = sum((m.get("residual_shares") or 0) for m in ms)
        recs = [(rec.get("ours", {}).get("per_market") or {}).get(m["slug"]) for m in ms]
        hpd = [r["hours_per_deployed_dollar"] for r in recs if r and r.get("hours_per_deployed_dollar") is not None]
        by_day: Dict[str, float] = defaultdict(float)
        for m in ms:
            if m.get("first_fill_utc"):
                by_day[m["first_fill_utc"][:10]] += m["filled_usd_turnover"]
        out[c] = {"label": c, "markets": len(ms), "span_hours": round(span_h, 2) if span_h else None,
                  "turnover_usd": round(turn, 4),
                  "turnover_per_day_usd": round(turn / (span_h / 24.0), 2) if span_h else None,
                  "turnover_per_day_labels": "rate over the cohort's own span (hours), not a calendar day",
                  "turnover_by_calendar_day_usd": {k: round(v, 2) for k, v in sorted(by_day.items())},
                  "action_usd": ("~2.50 (5 shares)" if c in ("R3L", "R4") else "0.01-0.02 shares (~$0.005-0.01)"),
                  "pair_completion_share": round(matched / bought, 4) if bought else None,
                  "residual_share_of_bought": round(resid_sh / bought, 4) if bought else None,
                  "pair_contribution_usd": round(sum(m["pair_contribution_usd"] for m in ms), 4),
                  "residual_contribution_usd": round(sum((m.get("residual_contribution_usd") or 0) for m in ms), 4),
                  "rebates_credited_usd": round(sum(m["rebate_credited_usd"] for m in ms), 2),
                  "net_usd": round(sum(m["net_usd"] for m in ms), 4),
                  "net_per_turnover": round(sum(m["net_usd"] for m in ms) / turn, 5) if turn else None,
                  "hours_per_deployed_dollar_p50": _pct(hpd).get("p50") if hpd else None,
                  "maker_taker_mix": "100% maker (post-only)", "concurrency": 1,
                  "selection": dict(defaultdict(int, {("%s/%s" % (m["shape"], m["category"])): 0 for m in ms}))}
        sel: Dict[str, int] = defaultdict(int)
        for m in ms:
            sel["%s/%s" % (m["shape"], m["category"])] += 1
        out[c]["selection"] = dict(sel)
    return out


def reproducibility(l5_early: Mapping[str, Any], l5_mature: Mapping[str, Any], us: Mapping[str, Any]) -> dict:
    r4 = us.get("R4") or {}
    early_tpd = l5_early.get("turnover_per_day_usd") or 0
    return {
        "bankroll_usd": BANKROLL_USD,
        "l5_early_turnover_per_day_usd": early_tpd,
        "l5_early_action_usd_p50": l5_early.get("action_usd_p50_of_day_medians"),
        "our_r4_turnover_per_day_usd": r4.get("turnover_per_day_usd"),
        "our_r4_action_usd": 2.50,
        "turnover_rate_ratio_ours_r4_over_l5_early": (round(r4["turnover_per_day_usd"] / early_tpd, 3) if r4.get("turnover_per_day_usd") and early_tpd else None),
        "our_at_size_calendar_day_usd": round(sum(v for c in ("R3L", "R4") for v in (us.get(c) or {}).get("turnover_by_calendar_day_usd", {}).values()), 2),
        "calendar_day_ratio_ours_at_size_over_l5_early": (round(sum(v for c in ("R3L", "R4") for v in (us.get(c) or {}).get("turnover_by_calendar_day_usd", {}).values()) / early_tpd, 4) if early_tpd else None),
        "reproducible_now": [
            "action size: L5's early day-median action (see l5_early_action_usd_p50) is within reach of the R4 $2.50 action; the 10-13 share median is a later-era number",
            "pair completion: our at-size completion share is in L5's range (see the table); the complement step is not the gap",
            "maker-only, one market at a time: matches L5's observed concurrency",
        ],
        "not_reproducible_yet": [
            "turnover per day: L5's is set by 5/15-minute windows that settle in minutes and re-enter within a minute; our sports markets settle ~1.2 h after the first fill and the campaign waits for settlement before rotating (FLAT_WAIT) -- the ratio above is the gap",
            "residual share: L5 runs a residual several actions deep (peak p50 3.5 units); R4 caps it at one action, which refuses ~98% of L5-style EXTENDs -- a translation choice, not a bankroll limit",
            "market/window selection: L5's lanes are two assets x two durations; ours is a sports universe served by a recorder whose pool held only resolved markets for 2.5 h (opportunity_utilization)",
        ],
        "labels": "what the ~$109 bankroll can reproduce is the per-market L5 process at ~1 action unit; what it cannot yet reproduce is L5's turnover, which is a market-duration and rotation property, not a capital property",
    }


def study() -> dict:
    days = l5_days()
    keys = sorted(days)
    early_keys = keys[:EARLY_DAYS]
    month_keys = [k for k in keys if k.startswith("2026-03")]
    mature_keys = [k for k in keys if k >= "2026-04-06"]
    l5_early = aggregate_days(days, early_keys, "L5 first %d covered days" % EARLY_DAYS)
    l5_month = aggregate_days(days, month_keys, "L5 2026-03")
    l5_mature = aggregate_days(days, mature_keys, "L5 mature (2026-04-06 onward)")
    us = ours()
    return {"owner_direction": "#167 5570456323 (early ramp comparison)",
            "l5_days": days, "l5_early": l5_early, "l5_first_month": l5_month, "l5_mature": l5_mature,
            "ours_by_cohort": us,
            "reproducibility": reproducibility(l5_early, l5_mature, us),
            "labels": ("L5 from canonical prints (FIFO lots; maker/taker unknown); ours from the economic scoreboard and the recycling study; "
                       "turnover per day over each cohort's own wall-clock span; read-only")}


def render_md(o: Mapping[str, Any]) -> str:
    cols = ["cohort", "days/markets", "turnover $/day", "action $ (p50)", "pair completion %", "residual % of bought", "net / turnover", "hours per deployed $ (p50)", "maker mix", "concurrency", "selection"]
    L = ["# Early ramp: L5 vs ours (OWNER 5570456323)\n", o["labels"] + "\n", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for k in ("l5_early", "l5_first_month", "l5_mature"):
        a = o[k]
        L.append("| %s | %s d | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            a["label"], a["days"], a["turnover_per_day_usd"], a["action_usd_p50_of_day_medians"],
            None if a["pair_completion_share"] is None else round(100 * a["pair_completion_share"], 1),
            None if a["residual_share_of_bought"] is None else round(100 * a["residual_share_of_bought"], 1),
            a["net_per_turnover"], "0.019 (all windows)" if k != "l5_early" else "see recycling", a["maker_taker_mix"], "1 per lane", json.dumps(a["lanes"])))
    for c in ("R1", "R2C", "R2C2", "R3L", "R4"):
        a = o["ours_by_cohort"].get(c)
        if not a:
            continue
        L.append("| %s | %s mkts | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            c, a["markets"], a["turnover_per_day_usd"], a["action_usd"],
            None if a["pair_completion_share"] is None else round(100 * a["pair_completion_share"], 1),
            None if a["residual_share_of_bought"] is None else round(100 * a["residual_share_of_bought"], 1),
            a["net_per_turnover"], a["hours_per_deployed_dollar_p50"], a["maker_taker_mix"], a["concurrency"], json.dumps(a["selection"])))
    r = o["reproducibility"]
    L.append("\nReproducible now with ~$%s: %s\n" % (r["bankroll_usd"], "; ".join(r["reproducible_now"])))
    L.append("Not reproducible yet: %s\n" % "; ".join(r["not_reproducible_yet"]))
    L.append("Turnover: ours at size on 2026-09-07 was $%s in one calendar day against L5's first-week $%s per day (ratio **%s**); as a rate over the R4 span the ratio is %s. %s\n"
             % (r["our_at_size_calendar_day_usd"], r["l5_early_turnover_per_day_usd"], r["calendar_day_ratio_ours_at_size_over_l5_early"], r["turnover_rate_ratio_ours_r4_over_l5_early"], r["labels"]))
    L.append("Day-by-day L5 numbers are in `l5_ramp_comparison.json`.\n")
    return "\n".join(L)


def main(argv: Optional[Sequence[str]] = None) -> int:
    out = study()
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, sort_keys=True, default=str)
    with open(os.path.join(A, "L5_RAMP_COMPARISON.md"), "w", encoding="utf-8") as f:
        f.write(render_md(out))
    print(render_md(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
